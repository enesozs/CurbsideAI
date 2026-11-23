"""Static artifacts for the Flows view, from gold daily OD.

Layout under ``web/public/data/flows/``:
- ``<source>/<YYYY-MM>.bin``: little-endian records, sorted by total trips desc, one per OD pair
  with ≥ MIN_TRIPS trips in the month:
  origin_idx u16, dest_idx u16 (zone column index; Z = "Outside NYC"), weekday_trips u32,
  weekend_trips u32, typical_minutes_x10 u16 (trip-weighted mean of daily median durations), pad u16.
- ``index.json``: per source and month: weekday/weekend day counts, pair counts, exact borough matrix
  (all trips, before the pair threshold), totals.

Edges are aggregates of observed trips between zones, drawn as schematic links — not routes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl

from curbside import artifacts, config, zones

MIN_TRIPS = 10
RECORD = np.dtype([("o", "<u2"), ("d", "<u2"), ("wd", "<u4"), ("we", "<u4"), ("dur", "<u2"), ("pad", "<u2")])
OUTSIDE = 265


def build(out: Path | None = None, months: list[str] | None = None) -> dict[str, Any]:
    out = out or artifacts.WEB_DATA / "flows"
    months = months or config.month_range(config.WINDOW_START, config.WINDOW_END)
    zt = pd.read_parquet(zones.zones_dir() / "zones.parquet").sort_values("LocationID")
    zone_ids = zt["LocationID"].astype(int).tolist()
    Z = len(zone_ids)
    col = {z: i for i, z in enumerate(zone_ids)} | {OUTSIDE: Z}
    borough_of = dict(zip(zt["LocationID"].astype(int), zt["borough"], strict=True)) | {
        OUTSIDE: "Outside NYC"
    }
    boroughs = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "EWR", "Outside NYC"]
    index: dict[str, Any] = {"min_trips": MIN_TRIPS, "outside_index": Z, "boroughs": boroughs, "sources": {}}

    for source in config.TRIP_SOURCES:
        (out / source).mkdir(parents=True, exist_ok=True)
        src_index = {}
        for month in months:
            p = config.gold_od_path(source, month)
            if not p.exists():
                continue
            od = pl.read_parquet(p).with_columns((pl.col("local_date").dt.weekday() >= 6).alias("weekend"))
            n_days = od.select(pl.col("local_date").n_unique()).item()
            days = od.select("local_date", "weekend").unique()
            n_we = int(days["weekend"].sum())
            agg = (
                od.group_by("origin_zone_id", "destination_zone_id")
                .agg(
                    pl.col("trip_count").filter(~pl.col("weekend")).sum().alias("wd"),
                    pl.col("trip_count").filter(pl.col("weekend")).sum().alias("we"),
                    ((pl.col("median_duration_s") * pl.col("stats_n")).sum() / pl.col("stats_n").sum()).alias(
                        "dur_s"
                    ),
                )
                .with_columns((pl.col("wd") + pl.col("we")).alias("total"))
                .sort("total", descending=True)
            )
            bm = np.zeros((len(boroughs), len(boroughs)), dtype=np.int64)
            for o, d, t in agg.select("origin_zone_id", "destination_zone_id", "total").iter_rows():
                bm[boroughs.index(borough_of[o]), boroughs.index(borough_of[d])] += t
            kept = agg.filter(pl.col("total") >= MIN_TRIPS)
            rec = np.zeros(kept.height, dtype=RECORD)
            rec["o"] = [col[int(z)] for z in kept["origin_zone_id"]]
            rec["d"] = [col[int(z)] for z in kept["destination_zone_id"]]
            rec["wd"] = kept["wd"].to_numpy()
            rec["we"] = kept["we"].to_numpy()
            dur = kept["dur_s"].fill_nan(None).fill_null(0).to_numpy()
            rec["dur"] = np.clip(np.round(dur / 6), 0, 65534)  # minutes × 10
            rec.tofile(out / source / f"{month}.bin")
            src_index[month] = {
                "days": n_days,
                "weekend_days": n_we,
                "weekday_days": n_days - n_we,
                "pairs": kept.height,
                "total_trips": int(agg["total"].sum()),
                "kept_trips": int(kept["total"].sum()),
                "borough_matrix": bm.tolist(),
            }
        index["sources"][source] = src_index
    (out / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    return {"bytes": sum(p.stat().st_size for p in out.rglob("*") if p.is_file())}
