"""Static artifacts for the Forecast Studio.

Layout under ``web/public/data/forecast/``:
- ``a/<source>/<YYYY-MM>.bin``: Task A day-ahead forecasts, uint16 little-endian, [hour][zone][3] =
  (point, lower, upper) rounded to whole trips; 65535 = no forecast. Hours align exactly with
  ``zh/<source>/<YYYY-MM>.bin`` (same start_utc, same count).
- ``b/<source>/<YYYY-MM>.bin``: frozen Task B forecasts, same encoding.
- ``index.json``: months available, metrics (Task A per fold/segment/model; Task B backtest and
  live status), model descriptions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from curbside import artifacts, config

NA = 65535


def _month_grid(source: str, month: str) -> tuple[np.ndarray, list[int]]:
    g = pl.read_parquet(config.gold_zone_hour_path(source, month), columns=["utc_hour", "pickup_zone_id"])
    utc = g["utc_hour"].unique().sort().to_numpy()
    zones = sorted(g["pickup_zone_id"].unique().to_list())
    return utc, zones


def _encode(df: pl.DataFrame, utc: np.ndarray, zones: list[int]) -> np.ndarray:
    T, Z = len(utc), len(zones)
    arr = np.full((T, Z, 3), NA, dtype=np.uint16)
    t_index = {int(u.astype("datetime64[s]").astype(np.int64)): i for i, u in enumerate(utc)}
    z_index = {z: i for i, z in enumerate(zones)}
    ts = df["target_hour_utc"].dt.epoch("s").to_numpy()
    ti = np.array([t_index.get(int(t), -1) for t in ts])
    zi = np.array([z_index.get(int(z), -1) for z in df["zone_id"].to_numpy()])
    ok = (ti >= 0) & (zi >= 0)
    for k, col in enumerate(["point_forecast", "lower", "upper"]):
        v = np.clip(np.round(df[col].to_numpy()[ok]), 0, NA - 1).astype(np.uint16)
        arr[ti[ok], zi[ok], k] = v
    return arr


def build(out: Path | None = None) -> dict[str, Any]:
    out = out or artifacts.WEB_DATA / "forecast"
    out.mkdir(parents=True, exist_ok=True)
    index: dict[str, Any] = {"task_a": {}, "task_b": {}}
    for source in config.TRIP_SOURCES:
        files = sorted((config.DATA / "forecasts" / "task_a" / source).glob("fold=*.parquet"))
        months_written = []
        if files:
            fa = pl.concat([pl.read_parquet(f) for f in files])
            local = fa["target_hour_utc"].dt.replace_time_zone("UTC").dt.convert_time_zone(config.LOCAL_TZ)
            fa = fa.with_columns(local.dt.strftime("%Y-%m").alias("month"))
            (out / "a" / source).mkdir(parents=True, exist_ok=True)
            for month in sorted(fa["month"].unique().to_list()):
                if not config.gold_zone_hour_path(source, month).exists():
                    continue
                utc, zones = _month_grid(source, month)
                _encode(fa.filter(pl.col("month") == month), utc, zones).tofile(
                    out / "a" / source / f"{month}.bin"
                )
                months_written.append(month)
        index["task_a"][source] = {"months": months_written}

        b_root = config.ROOT / "forecasts" / "task_b"
        live = []
        for man_path in sorted(b_root.glob(f"*/{source}.manifest.json")):
            man = json.loads(man_path.read_text())
            month = man["target_month"]
            fb = pl.read_parquet(man_path.parent / man["file"])
            from curbside import hours

            cal = hours.calendar(month, month).filter(pl.col("kind") != "nonexistent").sort("utc_hour")
            utc = cal["utc_hour"].to_numpy()
            zones = sorted(fb["zone_id"].unique().to_list())
            (out / "b" / source).mkdir(parents=True, exist_ok=True)
            _encode(fb, utc, zones).tofile(out / "b" / source / f"{month}.bin")
            live.append(
                {
                    **{
                        k: man[k]
                        for k in (
                            "target_month",
                            "issued_at_utc",
                            "information_cutoff_utc",
                            "sha256",
                            "model_id",
                        )
                    },
                    "start_utc": int(utc[0].astype("datetime64[s]").astype(np.int64)),
                    "hours": len(utc),
                }
            )
        index["task_b"][source] = {"live": live}

    m3 = config.REPORTS / "forecast"
    for name in ("task_a_metrics.json", "task_b_backtest.json", "task_b_scored.json"):
        if (m3 / name).exists():
            index[name.removesuffix(".json")] = json.loads((m3 / name).read_text())
    (out / "index.json").write_text(json.dumps(index, separators=(",", ":"), default=str))
    return {"bytes": sum(p.stat().st_size for p in out.rglob("*") if p.is_file())}
