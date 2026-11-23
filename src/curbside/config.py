"""Paths, source URLs and constants shared across the pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import duckdb

TripSource = Literal["yellow", "hvfhv"]
TRIP_SOURCES: tuple[TripSource, ...] = ("yellow", "hvfhv")

ROOT = Path(os.environ.get("CURBSIDE_ROOT", Path(__file__).resolve().parents[2]))
DATA = ROOT / "data"
RAW = DATA / "raw"
MANIFESTS = DATA / "manifests"
SILVER = DATA / "silver"
GOLD = DATA / "gold"
REPORTS = ROOT / "reports"
QA = REPORTS / "qa"

# Analysis window (inclusive). The end is extended as TLC publishes new months.
WINDOW_START = "2023-01"
# Latest ingested month. `curbside refresh` advances it by writing data/window.json (committed
# alongside the manifest); CURBSIDE_WINDOW_END overrides both.
_WINDOW_FILE = ROOT / "data" / "window.json"
WINDOW_END = os.environ.get(
    "CURBSIDE_WINDOW_END",
    __import__("json").loads(_WINDOW_FILE.read_text())["end"] if _WINDOW_FILE.exists() else "2026-09",
)

# Minimum trips behind a displayed HVFHV wait-time median (DATA_CONTRACT D9).
MIN_WAIT_N = 10
WAIT_MAX_S = 3600

# Silver validation flags (bitmask). See docs/DATA_CONTRACT.md.
FLAG_NEGATIVE_AMOUNT = 1
FLAG_NEGATIVE_DURATION = 2
FLAG_DURATION_OVER_24H = 4
FLAG_NON_POSITIVE_DISTANCE = 8
FLAG_FAR_OUTSIDE_FILE_MONTH = 16
FLAG_OUTSIDE_FILE_MONTH = 32
FLAG_DST_NONEXISTENT = 64
FLAG_UNKNOWN_ZONE = 128
FLAG_CATCHALL_PICKUP_ZONE = 256
FLAG_WAIT_INVALID = 512
FLAG_NULL_PICKUP = 1024

FLAG_NAMES = {
    FLAG_NEGATIVE_AMOUNT: "negative_amount",
    FLAG_NEGATIVE_DURATION: "negative_duration",
    FLAG_DURATION_OVER_24H: "duration_over_24h",
    FLAG_NON_POSITIVE_DISTANCE: "non_positive_distance",
    FLAG_FAR_OUTSIDE_FILE_MONTH: "pickup_far_outside_file_month",
    FLAG_OUTSIDE_FILE_MONTH: "pickup_outside_file_month",
    FLAG_DST_NONEXISTENT: "dst_nonexistent",
    FLAG_UNKNOWN_ZONE: "unknown_zone",
    FLAG_CATCHALL_PICKUP_ZONE: "catchall_pickup_zone",
    FLAG_WAIT_INVALID: "wait_invalid",
    FLAG_NULL_PICKUP: "null_pickup",
}

# Rows with any of these flags are not "completed trips" for counting (D8, provisional until
# reconciliation confirms it). Zone validity is handled separately in gold.
COUNT_EXCLUDE_MASK = FLAG_NEGATIVE_AMOUNT | FLAG_FAR_OUTSIDE_FILE_MONTH | FLAG_NULL_PICKUP
# Rows with these flags are excluded from duration / distance / fare statistics.
STATS_EXCLUDE_MASK = (
    COUNT_EXCLUDE_MASK | FLAG_NEGATIVE_DURATION | FLAG_DURATION_OVER_24H | FLAG_NON_POSITIVE_DISTANCE
)

CDN = "https://d37ci6vzurychx.cloudfront.net"

# TLC file prefixes differ from our trip_source names for HVFHV.
FILE_PREFIX: dict[TripSource, str] = {"yellow": "yellow", "hvfhv": "fhvhv"}

ZONE_FILES = {
    "taxi_zones.zip": f"{CDN}/misc/taxi_zones.zip",
    "taxi_zone_lookup.csv": f"{CDN}/misc/taxi_zone_lookup.csv",
}

LOCAL_TZ = "America/New_York"

# Zone IDs TLC uses as catch-alls: never mapped or used as forecast targets.
UNKNOWN_ZONE_IDS = frozenset({264, 265})

# Column names differ per source; the contract maps them to canonical roles.
PICKUP_COL: dict[TripSource, str] = {"yellow": "tpep_pickup_datetime", "hvfhv": "pickup_datetime"}
DROPOFF_COL: dict[TripSource, str] = {"yellow": "tpep_dropoff_datetime", "hvfhv": "dropoff_datetime"}

USER_AGENT = "curbside-pipeline/0.1 (+research; non-commercial)"


def trip_url(source: TripSource, month: str) -> str:
    """Official CloudFront URL for one monthly trip file, e.g. month='2024-11'."""
    validate_month(month)
    return f"{CDN}/trip-data/{FILE_PREFIX[source]}_tripdata_{month}.parquet"


def trip_path(source: TripSource, month: str) -> Path:
    return RAW / "tlc" / source / f"{FILE_PREFIX[source]}_tripdata_{month}.parquet"


def validate_month(month: str) -> None:
    parts = month.split("-")
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
        raise ValueError(f"month must look like YYYY-MM, got {month!r}")
    year, mon = int(parts[0]), int(parts[1])
    if not (2009 <= year <= 2100 and 1 <= mon <= 12):
        raise ValueError(f"month out of range: {month!r}")


def next_month(month: str) -> str:
    validate_month(month)
    year, mon = map(int, month.split("-"))
    year, mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
    return f"{year:04d}-{mon:02d}"


def prev_month(month: str) -> str:
    validate_month(month)
    year, mon = map(int, month.split("-"))
    year, mon = (year - 1, 12) if mon == 1 else (year, mon - 1)
    return f"{year:04d}-{mon:02d}"


def month_range(start: str, end: str) -> list[str]:
    out, m = [], start
    validate_month(end)
    while m <= end:
        out.append(m)
        m = next_month(m)
    return out


def silver_path(source: TripSource, month: str) -> Path:
    return SILVER / "trips" / f"trip_source={source}" / f"month={month}" / "part.parquet"


def gold_zone_hour_path(source: TripSource, month: str) -> Path:
    return GOLD / "zone_hour" / f"trip_source={source}" / f"month={month}" / "part.parquet"


def gold_od_path(source: TripSource, month: str) -> Path:
    return GOLD / "od_daily" / f"trip_source={source}" / f"month={month}" / "part.parquet"


def db() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB connection for pipeline steps (progress bar off to keep logs readable)."""
    con = duckdb.connect()
    con.execute("SET enable_progress_bar = false")
    return con
