"""Static web artifacts for the frontend: compact binaries + JSON, no server needed.

Layout under ``web/public/data/``:
- ``meta.json``: window, zones (in binary column order), per-month index, encodings, attribution
- ``zones.geojson``: simplified zone polygons (feature id = LocationID)
- ``zh/<source>/<YYYY-MM>.bin``: hourly pickups, little-endian uint16/uint32, row-major [hour][zone],
  hours contiguous in UTC from the month's ``start_utc``
- ``wait/<YYYY-MM>.bin``: HVFHV median wait seconds, uint16, 65535 = suppressed (wait_n < MIN_WAIT_N)
- ``daily/<source>.bin``: daily pickups by NY local date, uint32 [day][zone] from the window start
- ``qa.json``: trimmed pipeline QA summary for the Data Quality page

Only aggregates are published, never row-level trips (DATA_CONTRACT: privacy).
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from curbside import config, zones

WEB_DATA = config.ROOT / "web" / "public" / "data"
SUPPRESSED = 65535

ATTRIBUTION = [
    {
        "text": "Trip data: NYC Taxi & Limousine Commission (TLC) Trip Record Data",
        "url": "https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page",
    },
    {
        "text": "TLC makes no representations as to the accuracy of these data. "
        "Curbside is not affiliated with or endorsed by the City of New York or TLC.",
        "url": None,
    },
    {
        "text": "Map data © OpenStreetMap contributors (ODbL)",
        "url": "https://www.openstreetmap.org/copyright",
    },
    {"text": "Basemap: Protomaps", "url": "https://protomaps.com"},
    {
        "text": "Weather data by Open-Meteo.com, used (unmodified) as a forecast model input",
        "url": "https://open-meteo.com/",
    },
    {"text": "Open-Meteo data licence: CC BY 4.0", "url": "https://creativecommons.org/licenses/by/4.0/"},
    {"text": "Third-party software and font licences", "url": "/THIRD_PARTY_NOTICES.txt"},
]

SOURCE_LABELS = {
    "yellow": {"label": "Yellow taxi", "unit": "completed yellow-taxi pickups / hour"},
    "hvfhv": {"label": "Uber/Lyft", "unit": "completed Uber/Lyft (high-volume for-hire) pickups / hour"},
}


def _gold_month(source: str, month: str) -> pd.DataFrame:
    p = config.gold_zone_hour_path(source, month)
    return duckdb.execute(
        f"""SELECT epoch(utc_hour)::BIGINT AS t, pickup_zone_id AS z, trip_count, median_wait_s, wait_n,
                   data_quality_status
            FROM read_parquet('{p.as_posix()}') ORDER BY t, z"""
    ).df()


def build(out: Path = WEB_DATA, months: list[str] | None = None) -> dict[str, Any]:
    months = months or config.month_range(config.WINDOW_START, config.WINDOW_END)
    out.mkdir(parents=True, exist_ok=True)
    zt = pd.read_parquet(zones.zones_dir() / "zones.parquet").sort_values("LocationID")
    zone_ids = zt["LocationID"].astype(int).tolist()
    n_z = len(zone_ids)
    shutil.copyfile(zones.zones_dir() / "zones_web.geojson", out / "zones.geojson")

    meta: dict[str, Any] = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window": [months[0], months[-1]],
        "timezone": config.LOCAL_TZ,
        "zones": [
            {
                "id": int(r.LocationID),
                "zone": r.zone,
                "borough": r.borough,
                "area_km2": round(float(r.area_km2), 4),
                "label": [float(r.label_lon), float(r.label_lat)],
            }
            for r in zt.itertuples()
        ],
        "sources": {},
        "wait": {"min_n": config.MIN_WAIT_N, "suppressed": SUPPRESSED, "months": {}},
        "dst_split_hours_utc": [],
        "attribution": ATTRIBUTION,
    }

    daily_start = pd.Timestamp(f"{months[0]}-01")
    daily_end = pd.Timestamp(f"{config.next_month(months[-1])}-01")
    n_days = (daily_end - daily_start).days
    split_hours: set[int] = set()

    for source in config.TRIP_SOURCES:
        index: dict[str, Any] = {}
        daily = np.zeros((n_days, n_z), dtype=np.uint32)
        frames = {m: _gold_month(source, m) for m in months}
        peak = max(int(f["trip_count"].max()) for f in frames.values())
        dtype = np.uint16 if peak < 65535 else np.uint32
        (out / "zh" / source).mkdir(parents=True, exist_ok=True)
        for month, df in frames.items():
            hours = df["t"].unique()
            if not (np.diff(hours) == 3600).all():
                raise RuntimeError(f"non-contiguous UTC hours in gold {source} {month}")
            if df["z"].drop_duplicates().tolist() != zone_ids:
                raise RuntimeError(f"zone order mismatch in gold {source} {month}")
            mat = df["trip_count"].to_numpy().reshape(len(hours), n_z)
            mat.astype(dtype).astype(f"<{np.dtype(dtype).str[1:]}").tofile(
                out / "zh" / source / f"{month}.bin"
            )
            split_hours |= set(df.loc[df["data_quality_status"] == "dst_ambiguous_split", "t"].tolist())
            index[month] = {
                "start_utc": int(hours[0]),
                "hours": int(len(hours)),
                "total": int(mat.sum()),
                "max": int(mat.max()),
                "p99": float(np.percentile(mat[mat > 0], 99)) if (mat > 0).any() else 0.0,
            }
            local = pd.to_datetime(hours, unit="s", utc=True).tz_convert(config.LOCAL_TZ).tz_localize(None)
            day_idx = (local.normalize() - daily_start).days.to_numpy()
            np.add.at(daily, day_idx, mat.astype(np.uint32))

            if source == "hvfhv":
                w = df["median_wait_s"].to_numpy()
                ok = (df["wait_n"].to_numpy() >= config.MIN_WAIT_N) & ~np.isnan(w)
                wait = np.where(ok, np.clip(np.round(np.nan_to_num(w)), 0, SUPPRESSED - 1), SUPPRESSED)
                (out / "wait").mkdir(parents=True, exist_ok=True)
                wait.astype("<u2").reshape(len(hours), n_z).tofile(out / "wait" / f"{month}.bin")
                meta["wait"]["months"][month] = {"file": f"wait/{month}.bin"}

        (out / "daily").mkdir(parents=True, exist_ok=True)
        daily.astype("<u4").tofile(out / "daily" / f"{source}.bin")
        meta["sources"][source] = {
            **SOURCE_LABELS[source],
            "dtype": "uint16" if dtype == np.uint16 else "uint32",
            "peak_zone_hour": peak,
            "months": index,
            "daily": {"file": f"daily/{source}.bin", "start_date": months[0] + "-01", "days": n_days},
        }

    meta["dst_split_hours_utc"] = sorted(int(h) for h in split_hours)
    (out / "meta.json").write_text(json.dumps(meta, separators=(",", ":")))
    _write_qa(out)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    return {"out": str(out), "bytes": size, "months": len(months)}


def _write_qa(out: Path) -> None:
    if not ((config.QA / "qa_summary.json").exists() and (config.QA / "reconciliation.json").exists()):
        return
    qa = json.loads((config.QA / "qa_summary.json").read_text())
    recon = json.loads((config.QA / "reconciliation.json").read_text())
    trimmed = {"generated_at": qa["generated_at"], "window": qa["window"], "sources": {}}
    for source, v in qa["sources"].items():
        r = recon[source]
        best = r["variants"][r["best_variant"]]
        trimmed["sources"][source] = {
            "months": v["months"],
            "raw_rows_total": v["raw_rows_total"],
            "gold_trip_total": v["gold_trip_total"],
            "flag_totals": v["flag_totals"],
            "gold_invariants_pass": v["gold_invariants_pass"],
            "schema_versions": [
                {"months": s["months"], "n_columns": len(s["columns"])} for s in v["schema_versions"]
            ],
            "reconciliation": {
                "rule": r["best_variant"],
                "tolerance": r["tolerance"],
                "per_month": [
                    {k: m[k] for k in ("month", "aggregate_total", "our_total", "rel_diff")}
                    for m in best["per_month"]
                ],
            },
            "dst_checks": v["dst_checks"],
            "per_month": [
                {
                    k: m[k]
                    for k in ("month", "raw_rows", "counted_rows", "gold_trip_total", "catchall_pickups")
                }
                for m in v["per_month"]
            ],
        }
    (out / "qa.json").write_text(json.dumps(trimmed, separators=(",", ":")))
