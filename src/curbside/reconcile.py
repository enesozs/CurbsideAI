"""Reconcile our monthly pickup counts against TLC's own aggregate (NYC Open Data c5iv-bn4s).

We don't know a priori which counting convention TLC used for the aggregate (file month vs
pickup month; with or without negative-amount reversal rows). So we compute several candidate
conventions from silver and measure which one reproduces the aggregate. That settles the
provisional rule D8 with evidence instead of guesswork.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from curbside import config, fetch

INDUSTRY = {"yellow": "Yellow Taxi", "hvfhv": "FHV - High Volume"}
AGG_PATH = config.RAW / "opendata" / "c5iv-bn4s_pickups.csv"
TOLERANCE = 0.005  # monthly total relative difference allowed for a "match"
NEAR = 0.02  # up to this is "near"; beyond is "mismatch" (shown as a data-quality warning)


def month_status(rel_diff: float | None) -> str:
    if rel_diff is None:
        return "no_aggregate"
    d = abs(rel_diff)
    return "match" if d <= TOLERANCE else "near" if d <= NEAR else "mismatch"


def aggregate_url(start: str, end: str) -> str:
    where = (
        f"pickup_dropoff='Pick-up' AND metric_month >= '{start}-01' AND metric_month <= '{end}-01' "
        f"AND industry in ('{INDUSTRY['yellow']}', '{INDUSTRY['hvfhv']}')"
    )
    q = {
        "$select": "metric_month,industry,locationid,trip_count",
        "$where": where,
        "$limit": "200000",
        "$order": "metric_month,industry,locationid",
    }
    return "https://data.cityofnewyork.us/resource/c5iv-bn4s.csv?" + urlencode(q)


def fetch_aggregate(start: str, end: str, dest: Path = AGG_PATH) -> None:
    if dest.exists():
        dest.unlink()  # the aggregate is revised by TLC; always take a fresh, manifested copy
    fetch.download(aggregate_url(start, end), dest, dataset="opendata_c5iv-bn4s", period=f"{start}..{end}")


CANDIDATE_FLAGS = {
    "yellow": [
        config.FLAG_NEGATIVE_AMOUNT,
        config.FLAG_NON_POSITIVE_DISTANCE,
        config.FLAG_NEGATIVE_DURATION,
        config.FLAG_DURATION_OVER_24H,
    ],
    "hvfhv": [config.FLAG_NON_POSITIVE_DISTANCE, config.FLAG_NEGATIVE_DURATION, config.FLAG_WAIT_INVALID],
}


def variant_name(mask: int, month_kind: str) -> str:
    names = [config.FLAG_NAMES[b] for b in sorted(config.FLAG_NAMES) if mask & b]
    return ("excl_" + "+".join(names) if names else "all_rows") + f"_{month_kind}_month"


def _variants(source: config.TripSource) -> dict[str, tuple[str, str, int]]:
    """name -> (row filter, month expression, exclusion mask) for every subset of candidate flags."""
    base = config.FLAG_FAR_OUTSIDE_FILE_MONTH | config.FLAG_NULL_PICKUP
    months = {"file": "source_month", "pickup": "strftime(pickup_local_hour, '%Y-%m')"}
    cands = CANDIDATE_FLAGS[source]
    out = {}
    for subset in range(2 ** len(cands)):
        mask = sum(f for i, f in enumerate(cands) if subset >> i & 1)
        for kind, expr in months.items():
            out[variant_name(mask, kind)] = (f"flags & {base | mask} = 0", expr, mask)
    if source == "yellow":
        # Diagnostic: newer TPEP vendors (VendorID not in 1, 2) may be absent from TLC's aggregate.
        neg = base | config.FLAG_NEGATIVE_AMOUNT
        for kind, expr in months.items():
            out[f"excl_negative_amount+vendors_not_1_2_{kind}_month"] = (
                f"flags & {neg} = 0 AND vendor_id IN (1, 2)",
                expr,
                config.FLAG_NEGATIVE_AMOUNT,
            )
    return out


def reconcile(
    source: config.TripSource, months: list[str], silver_glob: str | None = None, agg_csv: Path = AGG_PATH
) -> dict[str, Any]:
    silver_glob = (
        silver_glob or (config.SILVER / "trips" / f"trip_source={source}" / "*/*.parquet").as_posix()
    )
    con = config.db()
    con.execute(
        f"""CREATE TEMP TABLE agg AS
            SELECT strftime(CAST(metric_month AS DATE), '%Y-%m') AS month, locationid::INTEGER AS zone,
                   sum(trip_count)::BIGINT AS n
            FROM read_csv('{agg_csv.as_posix()}', header=true)
            WHERE industry = '{INDUSTRY[source]}' GROUP BY 1, 2"""
    )
    months_sql = "(" + ", ".join(f"'{m}'" for m in months) + ")"
    results = {}
    for name, (where, month_expr, mask) in _variants(source).items():
        con.execute(
            f"""CREATE OR REPLACE TEMP TABLE ours AS
                SELECT {month_expr} AS month, pickup_zone_id::INTEGER AS zone, count(*)::BIGINT AS n
                FROM read_parquet('{silver_glob}') WHERE {where} GROUP BY 1, 2"""
        )
        per_month = con.execute(
            f"""WITH j AS (
                  SELECT coalesce(a.month, o.month) AS month, coalesce(a.zone, o.zone) AS zone,
                         coalesce(a.n, 0) AS agg, coalesce(o.n, 0) AS ours
                  FROM agg a FULL JOIN ours o ON a.month = o.month AND a.zone = o.zone)
                SELECT month, sum(agg), sum(ours), avg((agg = ours)::INT),
                       sum(abs(agg - ours))
                FROM j WHERE month IN {months_sql} GROUP BY 1 ORDER BY 1"""
        ).fetchall()
        months_out = [
            {
                "month": m,
                "aggregate_total": int(a),
                "our_total": int(o),
                "rel_diff": round((o - a) / a, 6) if a else None,
                "status": month_status((o - a) / a if a else None),
                "zone_exact_match_share": round(float(x), 4),
                "abs_zone_diff_sum": int(d),
            }
            for m, a, o, x, d in per_month
        ]
        diffs = [abs(r["rel_diff"]) for r in months_out if r["rel_diff"] is not None]
        results[name] = {
            "exclusion_mask": mask,
            "months_compared": len(months_out),
            "max_abs_rel_diff": max(diffs) if diffs else None,
            "median_abs_rel_diff": sorted(diffs)[len(diffs) // 2] if diffs else None,
            "months_within_tolerance": sum(d <= TOLERANCE for d in diffs),
            "mean_zone_exact_match_share": round(
                sum(r["zone_exact_match_share"] for r in months_out) / len(months_out), 4
            )
            if months_out
            else None,
            "all_months_within_tolerance": bool(diffs) and max(diffs) <= TOLERANCE,
            "per_month": months_out,
        }
    con.close()
    best = max(
        results,
        key=lambda k: (
            results[k]["months_within_tolerance"],
            -(results[k]["median_abs_rel_diff"] or 1),
            results[k]["mean_zone_exact_match_share"] or 0,
        ),
    )
    return {"trip_source": source, "tolerance": TOLERANCE, "best_variant": best, "variants": results}
