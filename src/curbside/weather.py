"""Vintage-safe weather features: Open-Meteo Previous Runs API, ``*_previous_day2`` (≥48 h lead).

Task A issues at 18:00 New York time on day D for the hours of D+1 (lead 6–30 h), so a value
predicted at least 48 h before its valid time was always available at issue (docs/forecasting.md).
GFS-seamless is used for its archive length; coverage per month and variable is recorded and
missing values stay null (never filled with observations). Data: CC BY 4.0, "Weather data by
Open-Meteo.com"; free tier is non-commercial (see README, Data and attribution).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from curbside import config, fetch

ENDPOINT = "https://previous-runs-api.open-meteo.com/v1/forecast"
LAT, LON = 40.7826, -73.9656  # Central Park
MODEL = "gfs_seamless"
VARIABLES = ["temperature_2m_previous_day2", "precipitation_previous_day2"]
RAW_DIR = config.RAW / "openmeteo" / "previous_runs"


def month_url(month: str) -> str:
    start = f"{month}-01"
    nm = config.next_month(month)
    # last day of month
    import datetime as _dt

    end = (_dt.date.fromisoformat(f"{nm}-01") - _dt.timedelta(days=1)).isoformat()
    return (
        f"{ENDPOINT}?latitude={LAT}&longitude={LON}&hourly={','.join(VARIABLES)}"
        f"&start_date={start}&end_date={end}&timezone=GMT&models={MODEL}"
    )


def fetch_month(month: str) -> Path:
    dest = RAW_DIR / f"{MODEL}_{month}.json"
    if not dest.exists():
        fetch.download(month_url(month), dest, dataset="openmeteo_previous_runs", period=month)
    return dest


def load(months: list[str]) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Hourly UTC weather features for the months (fetching any missing month)."""
    frames, coverage = [], {}
    for m in months:
        d = json.loads(fetch_month(m).read_text())
        h = d["hourly"]
        df = pl.DataFrame(
            {
                "utc_hour": pl.Series(h["time"]).str.to_datetime("%Y-%m-%dT%H:%M"),
                "temp_c": pl.Series(h[VARIABLES[0]], dtype=pl.Float64),
                "precip_mm": pl.Series(h[VARIABLES[1]], dtype=pl.Float64),
            }
        )
        coverage[m] = {
            "temp_c": round(1 - df["temp_c"].null_count() / df.height, 4),
            "precip_mm": round(1 - df["precip_mm"].null_count() / df.height, 4),
        }
        frames.append(df)
    return pl.concat(frames).unique("utc_hour").sort("utc_hour"), coverage
