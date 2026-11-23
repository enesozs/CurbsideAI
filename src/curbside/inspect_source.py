"""Source inspection: turn questions about a raw file into a reproducible report.

The report answers, for one monthly trip file:
- what the real columns and types are (naive vs tz-aware timestamps)
- row count, pickup range, rows outside the nominal month
- null shares for every column
- zone IDs outside the official lookup, and the 264/265 catch-all counts
- basic validity counts (negative durations, non-positive distance)
- DST evidence on transition days: are timestamps local wall time?
- for HVFHV: wait-time distribution and ``on_scene_datetime`` nulls per licensee
"""

from __future__ import annotations

import csv
import json
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from curbside import config

TZ = ZoneInfo(config.LOCAL_TZ)


def dst_transitions(month: str) -> list[dict[str, Any]]:
    """DST transitions in ``month`` for America/New_York.

    Returns entries like ``{"date": "2024-11-03", "kind": "fall_back", "local_hour": 1}``:
    spring_forward → the local hour that does not exist; fall_back → the hour that occurs twice.
    """
    config.validate_month(month)
    year, mon = map(int, month.split("-"))
    day = date(year, mon, 1)
    out = []
    while day.month == mon:
        start = datetime(day.year, day.month, day.day, tzinfo=TZ).utcoffset()
        nxt = day + timedelta(days=1)
        end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=TZ).utcoffset()
        if start != end:
            spring = end > start
            out.append(
                {
                    "date": day.isoformat(),
                    "kind": "spring_forward" if spring else "fall_back",
                    "local_hour": 2 if spring else 1,
                }
            )
        day = nxt
    return out


def load_zone_ids(lookup_csv: Path) -> set[int]:
    with lookup_csv.open(newline="") as f:
        return {int(r["LocationID"]) for r in csv.DictReader(f)}


def _q(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[tuple]:
    return con.execute(sql, params or []).fetchall()


def inspect_trip_file(source: config.TripSource, month: str, path: Path, lookup_csv: Path) -> dict[str, Any]:
    config.validate_month(month)
    con = config.db()
    con.execute(f"CREATE VIEW t AS SELECT * FROM read_parquet('{path.as_posix()}')")

    schema = [{"column": r[0], "type": r[1]} for r in _q(con, "DESCRIBE t")]
    columns = [c["column"] for c in schema]
    pu, do = config.PICKUP_COL[source], config.DROPOFF_COL[source]
    for col in (pu, do, "PULocationID", "DOLocationID"):
        if col not in columns:
            raise KeyError(f"expected column {col!r} missing from {path.name}; got {columns}")

    m_start = f"{month}-01 00:00:00"
    m_end = f"{config.next_month(month)}-01 00:00:00"
    (rows, pu_min, pu_max, before, after) = _q(
        con,
        f"""SELECT count(*), min({pu}), max({pu}),
                   count(*) FILTER ({pu} < TIMESTAMP '{m_start}'),
                   count(*) FILTER ({pu} >= TIMESTAMP '{m_end}')
            FROM t""",
    )[0]

    null_exprs = ", ".join(f'count(*) - count("{c}")' for c in columns)
    null_counts = _q(con, f"SELECT {null_exprs} FROM t")[0]
    null_share = {c: round(n / rows, 6) for c, n in zip(columns, null_counts, strict=True) if n}

    official = load_zone_ids(lookup_csv)
    zones: dict[str, Any] = {}
    for col in ("PULocationID", "DOLocationID"):
        seen = dict(_q(con, f'SELECT "{col}", count(*) FROM t GROUP BY 1'))
        zones[col] = {
            "distinct": len(seen),
            "not_in_lookup": {
                str(k): v
                for k, v in sorted(seen.items(), key=lambda kv: str(kv[0]))
                if k is None or int(k) not in official
            },
            "count_264": seen.get(264, 0),
            "count_265": seen.get(265, 0),
            "official_ids_never_seen": sorted(official - {int(k) for k in seen if k is not None}),
        }

    dist_col = "trip_distance" if source == "yellow" else "trip_miles"
    (neg_dur, over_24h, nonpos_dist) = _q(
        con,
        f"""SELECT count(*) FILTER ({do} < {pu}),
                   count(*) FILTER ({do} - {pu} > INTERVAL 24 HOUR),
                   count(*) FILTER ({dist_col} <= 0)
            FROM t""",
    )[0]

    hour_of_day = [int(r[1]) for r in _q(con, f"SELECT hour({pu}) h, count(*) FROM t GROUP BY 1 ORDER BY 1")]

    dst = [_dst_evidence(con, pu, tr) for tr in dst_transitions(month)]

    report: dict[str, Any] = {
        "trip_source": source,
        "month": month,
        "file": path.name,
        "file_bytes": path.stat().st_size,
        "rows": rows,
        "schema": schema,
        "pickup_column": pu,
        "pickup_min": str(pu_min),
        "pickup_max": str(pu_max),
        "rows_pickup_before_month": before,
        "rows_pickup_after_month": after,
        "null_share": null_share,
        "zones": zones,
        "validity": {
            "dropoff_before_pickup": neg_dur,
            "duration_over_24h": over_24h,
            f"{dist_col}_non_positive": nonpos_dist,
        },
        "pickups_by_wall_clock_hour": hour_of_day,
        "dst_evidence": dst,
    }

    if source == "yellow" and {"payment_type", "total_amount"} <= set(columns):
        report["yellow"] = {
            "payment_type_counts": {
                str(k): v for k, v in _q(con, "SELECT payment_type, count(*) FROM t GROUP BY 1 ORDER BY 1")
            },
            "total_amount_negative": _q(con, "SELECT count(*) FROM t WHERE total_amount < 0")[0][0],
            "total_amount_zero": _q(con, "SELECT count(*) FROM t WHERE total_amount = 0")[0][0],
        }
    if source == "hvfhv":
        report["hvfhv"] = _hvfhv_extras(con)
    con.close()
    return report


def _dst_evidence(con: duckdb.DuckDBPyConnection, pu: str, tr: dict[str, Any]) -> dict[str, Any]:
    """Compare the transition wall-clock hour with the same weekday/hour elsewhere in the month.

    If timestamps are naive local wall time, we expect ~0 pickups in the non-existent spring
    hour, and ~2x pickups in the repeated fall hour (two real hours share one label).
    """
    d = date.fromisoformat(tr["date"])
    h = tr["local_hour"]
    refs = [
        d + timedelta(days=7 * k) for k in range(-4, 5) if k and (d + timedelta(days=7 * k)).month == d.month
    ]

    def count(day: date) -> int:
        lo = datetime(day.year, day.month, day.day, h)
        return _q(con, f"SELECT count(*) FROM t WHERE {pu} >= ? AND {pu} < ?", [lo, lo + timedelta(hours=1)])[
            0
        ][0]

    on_day = count(d)
    ref_counts = {r.isoformat(): count(r) for r in refs}
    vals = sorted(ref_counts.values())
    median = (vals[len(vals) // 2] + vals[(len(vals) - 1) // 2]) / 2 if vals else 0
    return {
        **tr,
        "pickups_on_transition_day": on_day,
        "same_weekday_same_hour": ref_counts,
        "ratio_vs_median_reference": round(on_day / median, 3) if median else None,
    }


def _hvfhv_extras(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    on_scene = [
        {
            "licensee": r[0],
            "rows": r[1],
            "on_scene_null_share": round(r[2], 6),
            "request_null_share": round(r[3], 6),
        }
        for r in _q(
            con,
            """SELECT hvfhs_license_num, count(*),
                      avg((on_scene_datetime IS NULL)::INT), avg((request_datetime IS NULL)::INT)
               FROM t GROUP BY 1 ORDER BY 1""",
        )
    ]
    (p10, p50, p90, p99, neg, over_1h, n) = _q(
        con,
        """WITH w AS (SELECT epoch(pickup_datetime - request_datetime) s FROM t
                      WHERE request_datetime IS NOT NULL)
           SELECT quantile_cont(s, 0.1), quantile_cont(s, 0.5), quantile_cont(s, 0.9),
                  quantile_cont(s, 0.99), count(*) FILTER (s < 0), count(*) FILTER (s > 3600), count(*)
           FROM w""",
    )[0]
    return {
        "licensees": on_scene,
        "wait_seconds_pickup_minus_request": {
            "n": n,
            "p10": p10,
            "p50": p50,
            "p90": p90,
            "p99": p99,
            "negative": neg,
            "over_1h": over_1h,
        },
    }


def inspect_zones(zip_path: Path, lookup_csv: Path) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.namelist()
        shp = next(m for m in members if m.lower().endswith(".shp"))
        prj_name = next((m for m in members if m.lower().endswith(".prj")), None)
        prj = zf.read(prj_name).decode("utf-8", "replace") if prj_name else None

    con = config.db()
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(f"CREATE VIEW z AS SELECT * FROM ST_Read('/vsizip/{zip_path.as_posix()}/{shp}')")
    cols = [r[0] for r in _q(con, "DESCRIBE z")]
    n, n_ids = _q(con, "SELECT count(*), count(DISTINCT LocationID) FROM z")[0]
    dup = _q(con, "SELECT LocationID, count(*) FROM z GROUP BY 1 HAVING count(*) > 1 ORDER BY 1")
    invalid = _q(con, "SELECT count(*) FROM z WHERE NOT ST_IsValid(geom)")[0][0]
    bbox = _q(
        con, "SELECT min(ST_XMin(geom)), min(ST_YMin(geom)), max(ST_XMax(geom)), max(ST_YMax(geom)) FROM z"
    )[0]
    shp_ids = {int(r[0]) for r in _q(con, "SELECT DISTINCT LocationID FROM z")}
    con.close()

    with lookup_csv.open(newline="") as f:
        lookup = list(csv.DictReader(f))
    lookup_ids = {int(r["LocationID"]) for r in lookup}
    boroughs: dict[str, int] = {}
    for r in lookup:
        boroughs[r["Borough"]] = boroughs.get(r["Borough"], 0) + 1

    return {
        "zip_members": members,
        "prj": prj,
        "shape_columns": cols,
        "features": n,
        "distinct_location_ids": n_ids,
        "duplicate_location_ids": {str(k): v for k, v in dup},
        "invalid_geometries": invalid,
        "bbox_native_crs": bbox,
        "lookup_rows": len(lookup),
        "lookup_columns": list(lookup[0].keys()) if lookup else [],
        "lookup_boroughs": boroughs,
        "ids_in_lookup_not_in_shapes": sorted(lookup_ids - shp_ids),
        "ids_in_shapes_not_in_lookup": sorted(shp_ids - lookup_ids),
    }


def write_sample(path: Path, out_csv: Path, n: int = 20, seed: int = 42) -> int:
    """Reproducible random sample of raw rows (TLC trip files carry no personal identifiers)."""
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    con = config.db()
    con.execute(
        f"COPY (SELECT * FROM read_parquet('{path.as_posix()}') USING SAMPLE reservoir({n} ROWS) "
        f"REPEATABLE ({seed})) TO '{out_csv.as_posix()}' (HEADER)"
    )
    con.close()
    return n


def write_report(report: dict[str, Any], out_json: Path) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, default=str) + "\n")
