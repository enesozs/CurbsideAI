"""Gold layer: zone-hour counts (+ HVFHV wait times) and daily origin–destination tables.

Gold month M is built from silver months M-1, M, M+1 filtered to pickups whose local time is
in M, so trips reported in a neighbouring file land in the right hour. Every (zone, hour) of
an observed month gets a row: no trips → explicit zero. The fall-back repeated local hour is
split into its two UTC hours (ceil/floor) and flagged ``dst_ambiguous_split``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from curbside import config, hours


def _silver_inputs(source: config.TripSource, month: str) -> list[Path]:
    months = [config.prev_month(month), month, config.next_month(month)]
    return [p for m in months if (p := config.silver_path(source, m)).exists()]


def _sql_list(paths: list[Path]) -> str:
    return "[" + ", ".join(f"'{p.as_posix()}'" for p in paths) + "]"


def build_zone_hour(
    source: config.TripSource,
    month: str,
    zone_ids: list[int],
    out_path: Path | None = None,
    silver_paths: list[Path] | None = None,
) -> dict[str, Any]:
    out_path = out_path or config.gold_zone_hour_path(source, month)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    inputs = silver_paths if silver_paths is not None else _silver_inputs(source, month)
    if not inputs:
        raise FileNotFoundError(f"no silver input for {source} {month}")

    con = config.db()
    cal = hours.calendar(month, month).filter(pl.col("kind") != "nonexistent")
    con.register("cal", cal.to_arrow())
    con.execute(f"CREATE TEMP TABLE z AS SELECT unnest({list(zone_ids)})::SMALLINT AS zone_id")
    m_start, m_end = f"{month}-01", f"{config.next_month(month)}-01"
    con.execute(
        f"""CREATE TEMP VIEW s AS
            SELECT * FROM read_parquet({_sql_list(inputs)})
            WHERE flags & {config.COUNT_EXCLUDE_MASK} = 0
              AND pickup_local_hour >= TIMESTAMP '{m_start}' AND pickup_local_hour < TIMESTAMP '{m_end}'"""
    )
    con.execute(
        f"""COPY (
          WITH b AS (
            SELECT pickup_zone_id AS zone_id, pickup_local_hour AS local_hour, count(*) AS n,
                   median(wait_s) FILTER (wait_s IS NOT NULL AND flags & {config.FLAG_WAIT_INVALID} = 0)
                       AS med_wait,
                   count(*) FILTER (wait_s IS NOT NULL AND flags & {config.FLAG_WAIT_INVALID} = 0)
                       AS wait_n
            FROM s WHERE pickup_zone_id IN (SELECT zone_id FROM z)
            GROUP BY 1, 2
          )
          SELECT cal.utc_hour,
                 cal.local_hour_label,
                 cal.utc_offset_min,
                 cal.fold,
                 z.zone_id AS pickup_zone_id,
                 '{source}' AS trip_source,
                 (CASE WHEN cal.kind = 'ambiguous' AND cal.fold = 0 THEN ceil(coalesce(b.n, 0) / 2)
                       WHEN cal.kind = 'ambiguous' THEN floor(coalesce(b.n, 0) / 2)
                       ELSE coalesce(b.n, 0) END)::INTEGER AS trip_count,
                 b.med_wait::DOUBLE AS median_wait_s,
                 (CASE WHEN cal.kind = 'ambiguous' AND cal.fold = 0 THEN ceil(coalesce(b.wait_n, 0) / 2)
                       WHEN cal.kind = 'ambiguous' THEN floor(coalesce(b.wait_n, 0) / 2)
                       ELSE coalesce(b.wait_n, 0) END)::INTEGER AS wait_n,
                 CASE WHEN cal.kind = 'ambiguous' THEN 'dst_ambiguous_split' ELSE 'ok' END
                     AS data_quality_status,
                 'observed' AS source_coverage
          FROM cal CROSS JOIN z
          LEFT JOIN b ON b.zone_id = z.zone_id AND b.local_hour = cal.local_hour
          ORDER BY cal.utc_hour, z.zone_id
        ) TO '{out_path.as_posix()}' (FORMAT parquet, COMPRESSION zstd)"""
    )

    gold_total, gold_rows, keys = con.execute(
        f"""SELECT sum(trip_count), count(*), count(DISTINCT (utc_hour, pickup_zone_id))
            FROM read_parquet('{out_path.as_posix()}')"""
    ).fetchone()
    expected, catchall, unknown = con.execute(
        f"""SELECT count(*) FILTER (pickup_zone_id IN (SELECT zone_id FROM z)),
                   count(*) FILTER (flags & {config.FLAG_CATCHALL_PICKUP_ZONE} <> 0),
                   count(*) FILTER (pickup_zone_id NOT IN (SELECT zone_id FROM z)
                                    AND flags & {config.FLAG_CATCHALL_PICKUP_ZONE} = 0)
            FROM s"""
    ).fetchone()
    con.close()

    result = {
        "trip_source": source,
        "month": month,
        "gold_rows": gold_rows,
        "expected_rows": len(cal) * len(zone_ids),
        "unique_keys": keys,
        "gold_trip_total": int(gold_total or 0),
        "silver_counted_in_zone_universe": expected,
        "silver_counted_catchall_pickup": catchall,
        "silver_counted_unknown_pickup": unknown,
        "silver_inputs": [p.parent.name for p in inputs],
    }
    result["totals_reconcile"] = result["gold_trip_total"] == expected
    result["keys_unique"] = keys == gold_rows == result["expected_rows"]
    if not (result["totals_reconcile"] and result["keys_unique"]):
        raise RuntimeError(f"gold invariant failed for {source} {month}: {result}")
    return result


def build_od_daily(
    source: config.TripSource,
    month: str,
    zone_ids: list[int],
    out_path: Path | None = None,
    silver_paths: list[Path] | None = None,
) -> dict[str, Any]:
    """Daily OD by local pickup date. Destination 265 is kept as 'Outside NYC' (D7)."""
    out_path = out_path or config.gold_od_path(source, month)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    inputs = silver_paths if silver_paths is not None else _silver_inputs(source, month)
    zones_sql = "(" + ", ".join(str(z) for z in zone_ids) + ")"
    m_start, m_end = f"{month}-01", f"{config.next_month(month)}-01"
    con = config.db()
    con.execute(
        f"""COPY (
          SELECT CAST(pickup_local_hour AS DATE) AS local_date,
                 pickup_zone_id AS origin_zone_id,
                 dropoff_zone_id AS destination_zone_id,
                 '{source}' AS trip_source,
                 count(*)::INTEGER AS trip_count,
                 median(trip_duration_s) FILTER (flags & {config.STATS_EXCLUDE_MASK} = 0)
                     AS median_duration_s,
                 median(trip_distance_miles) FILTER (flags & {config.STATS_EXCLUDE_MASK} = 0)
                     AS median_distance_miles,
                 median(fare_usd) FILTER (flags & {config.STATS_EXCLUDE_MASK} = 0 AND fare_usd > 0)
                     AS median_fare_usd,
                 count(*) FILTER (flags & {config.STATS_EXCLUDE_MASK} = 0)::INTEGER AS stats_n
          FROM read_parquet({_sql_list(inputs)})
          WHERE flags & {config.COUNT_EXCLUDE_MASK} = 0
            AND pickup_local_hour >= TIMESTAMP '{m_start}' AND pickup_local_hour < TIMESTAMP '{m_end}'
            AND pickup_zone_id IN {zones_sql}
            AND (dropoff_zone_id IN {zones_sql} OR dropoff_zone_id = 265)
          GROUP BY 1, 2, 3
          ORDER BY 1, 2, 3
        ) TO '{out_path.as_posix()}' (FORMAT parquet, COMPRESSION zstd)"""
    )
    rows, total = con.execute(
        f"SELECT count(*), sum(trip_count) FROM read_parquet('{out_path.as_posix()}')"
    ).fetchone()
    con.close()
    return {"trip_source": source, "month": month, "od_rows": rows, "od_trip_total": int(total or 0)}
