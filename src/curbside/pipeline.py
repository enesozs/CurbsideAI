"""Warehouse orchestration: raw → silver → gold → reconciliation → QA report, plus gated raw pruning."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from curbside import config, fetch, gold, hours, reconcile, silver, zones
from curbside.inspect_source import dst_transitions


def _manifest_sha(url: str) -> str | None:
    for row in reversed(fetch.read_manifest()):
        if row["url"] == url:
            return row["sha256"]
    return None


def _write_json(obj: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str) + "\n")


def build_zones() -> dict[str, Any]:
    zdir = config.RAW / "tlc" / "zones"
    result = zones.build_zones(zdir / "taxi_zones.zip", zdir / "taxi_zone_lookup.csv")
    _write_json(result, config.QA / "zones_build.json")
    return result


def build_silver_all(sources: list[config.TripSource], months: list[str], log=print) -> None:
    zone_ids = zones.zone_ids()
    nonexistent = hours.nonexistent_hours(config.prev_month(months[0]), config.next_month(months[-1]))
    for source in sources:
        for month in months:
            raw = config.trip_path(source, month)
            sha = _manifest_sha(config.trip_url(source, month))
            if sha and silver.silver_is_current(source, month, sha):
                continue
            if not raw.exists():
                raise FileNotFoundError(f"raw file missing and silver not current: {raw}")
            if sha is None:
                raise RuntimeError(f"{raw.name} not in manifest; fetch it with the pipeline")
            stats = silver.build_silver(source, month, raw, zone_ids, sha, nonexistent=nonexistent)
            log(f"silver {source} {month}: {stats['silver_rows']:,} rows, {stats['counted_rows']:,} counted")


def build_gold_all(sources: list[config.TripSource], months: list[str], log=print) -> None:
    zone_ids = zones.zone_ids()
    for source in sources:
        for month in months:
            zh = gold.build_zone_hour(source, month, zone_ids)
            od = gold.build_od_daily(source, month, zone_ids)
            _write_json({**zh, **od}, config.QA / "gold" / f"{source}_{month}.json")
            log(f"gold {source} {month}: {zh['gold_trip_total']:,} trips, {od['od_rows']:,} OD rows")


def run_reconciliation(sources: list[config.TripSource], months: list[str]) -> dict[str, Any]:
    reconcile.fetch_aggregate(months[0], months[-1])
    out = {s: reconcile.reconcile(s, months) for s in sources}
    _write_json(out, config.QA / "reconciliation.json")
    return out


def _dst_checks(source: config.TripSource, months: list[str]) -> list[dict[str, Any]]:
    """Per DST transition in the window: evidence that timestamps stay naive local time."""
    con = config.db()
    out = []
    for month in months:
        for tr in dst_transitions(month):
            files = [
                p.as_posix()
                for m in (config.prev_month(month), month, config.next_month(month))
                if (p := config.silver_path(source, m)).exists()
            ]
            d = datetime.fromisoformat(tr["date"])
            hour = d + timedelta(hours=tr["local_hour"])
            refs = [hour + timedelta(days=7 * k) for k in (-2, -1, 1, 2)]
            raw_hour_counts = {}
            for h in [hour, *refs]:
                raw_hour_counts[h] = con.execute(
                    f"""SELECT count(*) FROM read_parquet({files})
                        WHERE pickup_ts_raw >= ? AND pickup_ts_raw < ?""",
                    [h, h + timedelta(hours=1)],
                ).fetchone()[0]
            ref_median = statistics.median(raw_hour_counts[h] for h in refs)
            out.append(
                {
                    **tr,
                    "raw_pickups_in_wall_clock_hour": raw_hour_counts[hour],
                    "reference_median": ref_median,
                    "ratio": round(raw_hour_counts[hour] / ref_median, 3) if ref_median else None,
                    "expected": "~0" if tr["kind"] == "spring_forward" else "~2",
                }
            )
    con.close()
    return out


def write_qa_summary(sources: list[config.TripSource], months: list[str]) -> dict[str, Any]:
    recon_path = config.QA / "reconciliation.json"
    recon = json.loads(recon_path.read_text()) if recon_path.exists() else {}
    summary: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window": [months[0], months[-1]],
        "sources": {},
    }
    for source in sources:
        rows, flags, schemas = [], defaultdict(int), defaultdict(list)
        gold_ok = True
        for month in months:
            st = json.loads(silver.stats_path(source, month).read_text())
            gq = json.loads((config.QA / "gold" / f"{source}_{month}.json").read_text())
            gold_ok &= gq["totals_reconcile"] and gq["keys_unique"]
            for k, v in st["flag_counts"].items():
                flags[k] += v
            schemas[tuple(st["raw_columns"])].append(month)
            rows.append(
                {
                    "month": month,
                    "raw_rows": st["raw_rows"],
                    "counted_rows": st["counted_rows"],
                    "gold_trip_total": gq["gold_trip_total"],
                    "catchall_pickups": gq["silver_counted_catchall_pickup"],
                    "unknown_zone_pickups": gq["silver_counted_unknown_pickup"],
                    "od_trip_total": gq["od_trip_total"],
                    "flag_counts": st["flag_counts"],
                }
            )
        r = recon.get(source, {})
        best = r.get("variants", {}).get(r.get("best_variant"), {})
        summary["sources"][source] = {
            "months": len(rows),
            "raw_rows_total": sum(x["raw_rows"] for x in rows),
            "gold_trip_total": sum(x["gold_trip_total"] for x in rows),
            "flag_totals": dict(flags),
            "gold_invariants_pass": gold_ok,
            "schema_versions": [{"columns": list(cols), "months": ms} for cols, ms in schemas.items()],
            "reconciliation_best_variant": r.get("best_variant"),
            "reconciliation_max_abs_rel_diff": best.get("max_abs_rel_diff"),
            "reconciliation_within_tolerance": best.get("all_months_within_tolerance"),
            "dst_checks": _dst_checks(source, months),
            "per_month": rows,
        }
    _write_json(summary, config.QA / "qa_summary.json")
    return summary


def prune_raw(source: config.TripSource, months: list[str], log=print) -> list[str]:
    """Delete raw files whose silver is current, gold invariants pass, and reconciliation passed."""
    if source != "hvfhv":
        raise ValueError("retention policy D11: only HVFHV raw files are pruned")
    recon = json.loads((config.QA / "reconciliation.json").read_text())[source]
    best = recon["variants"][recon["best_variant"]]
    ok_months = {
        r["month"]
        for r in best["per_month"]
        if r["rel_diff"] is not None and abs(r["rel_diff"]) <= recon["tolerance"]
    }
    pruned = []
    for month in months:
        raw = config.trip_path(source, month)
        if not raw.exists():
            continue
        sha = _manifest_sha(config.trip_url(source, month))
        gq_path = config.QA / "gold" / f"{source}_{month}.json"
        gq = json.loads(gq_path.read_text()) if gq_path.exists() else {}
        gates = {
            "silver_current": bool(sha) and silver.silver_is_current(source, month, sha),
            "gold_invariants": gq.get("totals_reconcile") and gq.get("keys_unique"),
            "reconciled": month in ok_months,
        }
        if all(gates.values()):
            raw.unlink()
            pruned.append(month)
        else:
            log(f"keep {raw.name}: gates {gates}")
    return pruned
