"""Silver layer: one validated, flagged row per raw trip, in a schema shared by both sources.

Nothing is silently dropped here: every raw row lands in silver with a ``flags`` bitmask.
Counting rules (``config.COUNT_EXCLUDE_MASK``) and zone validity are applied in gold.
Each build also writes a stats JSON (input rows, rows per flag, schema) for the QA report.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from curbside import config, hours

SILVER_VERSION = 2  # bump when the silver schema or flag logic changes; forces a rebuild

SILVER_COLUMNS = [
    "trip_source",
    "source_month",
    "source_file_sha256",
    "pickup_ts_raw",
    "dropoff_ts_raw",
    "request_ts_raw",
    "pickup_local_hour",
    "pickup_zone_id",
    "dropoff_zone_id",
    "trip_distance_miles",
    "trip_duration_s",
    "wait_s",
    "fare_usd",
    "total_amount_usd",
    "payment_type",
    "vendor_id",
    "hvfhs_license_num",
    "cbd_congestion_fee_usd",
    "flags",
]


def stats_path(source: config.TripSource, month: str) -> Path:
    return config.QA / "silver" / f"{source}_{month}.json"


def _col(columns: dict[str, str], name: str) -> str | None:
    """Case-insensitive column lookup (TLC changes casing across years, e.g. Airport_fee)."""
    real = columns.get(name.lower())
    return f'"{real}"' if real else None


def build_silver(
    source: config.TripSource,
    month: str,
    raw_path: Path,
    zone_ids: list[int],
    sha256: str,
    out_path: Path | None = None,
    nonexistent: list[datetime] | None = None,
) -> dict[str, Any]:
    out_path = out_path or config.silver_path(source, month)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    con = config.db()
    con.execute(f"CREATE VIEW raw AS SELECT * FROM read_parquet('{raw_path.as_posix()}')")
    described = con.execute("DESCRIBE raw").fetchall()
    cols = {r[0].lower(): r[0] for r in described}

    pu = _col(cols, config.PICKUP_COL[source])
    do = _col(cols, config.DROPOFF_COL[source])
    if not (pu and do and _col(cols, "PULocationID") and _col(cols, "DOLocationID")):
        raise KeyError(f"{raw_path.name}: required columns missing; got {sorted(cols.values())}")

    null = "NULL"
    if source == "yellow":
        dist = _col(cols, "trip_distance")
        fare = _col(cols, "fare_amount") or null
        total = _col(cols, "total_amount") or null
        payment = _col(cols, "payment_type") or null
        vendor = _col(cols, "VendorID") or null
        request = null
        license_ = null
    else:
        dist = _col(cols, "trip_miles")
        fare = _col(cols, "base_passenger_fare") or null
        total = null
        payment = null
        vendor = null
        request = _col(cols, "request_datetime") or null
        license_ = _col(cols, "hvfhs_license_num") or null
    cbd = _col(cols, "cbd_congestion_fee") or null

    m_start = f"TIMESTAMP '{month}-01 00:00:00'"
    m_end = f"TIMESTAMP '{config.next_month(month)}-01 00:00:00'"
    nonexist = nonexistent if nonexistent is not None else hours.nonexistent_hours(month, month)
    nonexist_sql = (
        "(" + ", ".join(f"TIMESTAMP '{h:%Y-%m-%d %H:00:00}'" for h in nonexist) + ")" if nonexist else None
    )
    is_nonexist = f"date_trunc('hour', {pu}) IN {nonexist_sql}" if nonexist_sql else "FALSE"
    zones_sql = "(" + ", ".join(str(z) for z in zone_ids) + ")"
    catchall_sql = "(" + ", ".join(str(z) for z in sorted(config.UNKNOWN_ZONE_IDS)) + ")"
    wait_expr = f"epoch({pu} - {request})" if request != null else "NULL"

    F = config
    flags_expr = f"""(
        CASE WHEN {total} < 0 THEN {F.FLAG_NEGATIVE_AMOUNT} ELSE 0 END
      + CASE WHEN {do} < {pu} THEN {F.FLAG_NEGATIVE_DURATION} ELSE 0 END
      + CASE WHEN {do} - {pu} > INTERVAL 24 HOUR THEN {F.FLAG_DURATION_OVER_24H} ELSE 0 END
      + CASE WHEN coalesce({dist}, 0) <= 0 THEN {F.FLAG_NON_POSITIVE_DISTANCE} ELSE 0 END
      + CASE WHEN {pu} < {m_start} - INTERVAL 1 DAY OR {pu} >= {m_end} + INTERVAL 1 DAY
             THEN {F.FLAG_FAR_OUTSIDE_FILE_MONTH}
             WHEN {pu} < {m_start} OR {pu} >= {m_end} THEN {F.FLAG_OUTSIDE_FILE_MONTH} ELSE 0 END
      + CASE WHEN {is_nonexist} THEN {F.FLAG_DST_NONEXISTENT} ELSE 0 END
      + CASE WHEN "PULocationID" NOT IN {zones_sql} AND "PULocationID" NOT IN {catchall_sql}
               OR "DOLocationID" NOT IN {zones_sql} AND "DOLocationID" NOT IN {catchall_sql}
               OR "PULocationID" IS NULL OR "DOLocationID" IS NULL
             THEN {F.FLAG_UNKNOWN_ZONE} ELSE 0 END
      + CASE WHEN "PULocationID" IN {catchall_sql} THEN {F.FLAG_CATCHALL_PICKUP_ZONE} ELSE 0 END
      + CASE WHEN {wait_expr} IS NULL AND '{source}' = 'hvfhv'
               OR {wait_expr} < 0 OR {wait_expr} > {F.WAIT_MAX_S}
             THEN {F.FLAG_WAIT_INVALID} ELSE 0 END
      + CASE WHEN {pu} IS NULL THEN {F.FLAG_NULL_PICKUP} ELSE 0 END
    )::INTEGER"""

    con.execute(
        f"""COPY (
          SELECT '{source}' AS trip_source,
                 '{month}' AS source_month,
                 '{sha256}' AS source_file_sha256,
                 {pu}::TIMESTAMP AS pickup_ts_raw,
                 {do}::TIMESTAMP AS dropoff_ts_raw,
                 {request}::TIMESTAMP AS request_ts_raw,
                 date_trunc('hour', {pu}) + CASE WHEN {is_nonexist} THEN INTERVAL 1 HOUR
                                                 ELSE INTERVAL 0 HOUR END AS pickup_local_hour,
                 "PULocationID"::SMALLINT AS pickup_zone_id,
                 "DOLocationID"::SMALLINT AS dropoff_zone_id,
                 {dist}::DOUBLE AS trip_distance_miles,
                 epoch({do} - {pu})::INTEGER AS trip_duration_s,
                 {wait_expr}::INTEGER AS wait_s,
                 {fare}::DOUBLE AS fare_usd,
                 {total}::DOUBLE AS total_amount_usd,
                 {payment}::SMALLINT AS payment_type,
                 {vendor}::SMALLINT AS vendor_id,
                 {license_}::VARCHAR AS hvfhs_license_num,
                 {cbd}::DOUBLE AS cbd_congestion_fee_usd,
                 {flags_expr} AS flags
          FROM raw
        ) TO '{out_path.as_posix()}' (FORMAT parquet, COMPRESSION zstd)"""
    )

    flag_counts = {}
    for bit, name in config.FLAG_NAMES.items():
        flag_counts[name] = con.execute(
            f"SELECT count(*) FROM read_parquet('{out_path.as_posix()}') WHERE flags & {bit} <> 0"
        ).fetchone()[0]
    raw_rows = con.execute("SELECT count(*) FROM raw").fetchone()[0]
    silver_rows, counted = con.execute(
        f"""SELECT count(*), count(*) FILTER (flags & {config.COUNT_EXCLUDE_MASK} = 0)
            FROM read_parquet('{out_path.as_posix()}')"""
    ).fetchone()
    provider_col = "vendor_id" if source == "yellow" else "hvfhs_license_num"
    provider_counts = {
        str(k): v
        for k, v in con.execute(
            f"SELECT {provider_col}, count(*) FROM read_parquet('{out_path.as_posix()}') "
            "GROUP BY 1 ORDER BY 1"
        ).fetchall()
    }
    con.close()

    stats = {
        "trip_source": source,
        "month": month,
        "raw_file": raw_path.name,
        "raw_sha256": sha256,
        "raw_rows": raw_rows,
        "silver_rows": silver_rows,
        "counted_rows": counted,
        "flag_counts": flag_counts,
        "provider_counts": provider_counts,
        "raw_columns": [r[0] for r in described],
        "raw_types": {r[0]: r[1] for r in described},
        "silver_version": SILVER_VERSION,
        "built_at": datetime.now().isoformat(timespec="seconds"),
    }
    sp = stats_path(source, month)
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(stats, indent=2) + "\n")
    if raw_rows != silver_rows:
        raise RuntimeError(f"silver row count {silver_rows} != raw {raw_rows} for {source} {month}")
    return stats


def silver_is_current(source: config.TripSource, month: str, sha256: str) -> bool:
    sp = stats_path(source, month)
    if not (config.silver_path(source, month).exists() and sp.exists()):
        return False
    st = json.loads(sp.read_text())
    return st.get("raw_sha256") == sha256 and st.get("silver_version") == SILVER_VERSION
