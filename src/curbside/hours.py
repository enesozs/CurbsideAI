"""Hour calendar: maps naive America/New_York wall-clock hours to canonical UTC hours.

TLC timestamps are naive local wall time (source-audit evidence). Each local hour bucket maps to:
- one UTC hour ('normal'),
- two UTC hours on the fall-back night ('ambiguous': fold 0 = first/EDT, fold 1 = second/EST),
- or nothing on the spring-forward night ('nonexistent'); rows stamped there are moved to the
  next local hour in silver (zoneinfo fold=0 semantics), so the calendar marks but never uses it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import polars as pl

from curbside import config

TZ = ZoneInfo(config.LOCAL_TZ)


def _utc_naive(local: datetime, fold: int) -> datetime:
    return local.replace(tzinfo=TZ, fold=fold).astimezone(UTC).replace(tzinfo=None)


def classify(local: datetime) -> str:
    """'normal' | 'ambiguous' | 'nonexistent' for a naive local wall-clock time."""
    u0, u1 = _utc_naive(local, 0), _utc_naive(local, 1)
    if u0 != u1:
        # Both folds give different instants: either a repeated hour or a gap.
        back = (
            u0.replace(tzinfo=UTC).astimezone(TZ).replace(tzinfo=None),
            u1.replace(tzinfo=UTC).astimezone(TZ).replace(tzinfo=None),
        )
        return "ambiguous" if back == (local, local) else "nonexistent"
    return "normal"


def calendar(start_month: str, end_month: str) -> pl.DataFrame:
    """One row per (local_hour, fold) for every local hour in [start_month, end_month]."""
    start = datetime.fromisoformat(f"{start_month}-01")
    end = datetime.fromisoformat(f"{config.next_month(end_month)}-01")
    rows = []
    h = start
    while h < end:
        kind = classify(h)
        folds = (0, 1) if kind == "ambiguous" else (0,)
        for fold in folds:
            aware = h.replace(tzinfo=TZ, fold=fold)
            offset_min = int(aware.utcoffset().total_seconds() // 60)
            rows.append(
                {
                    "local_hour": h,
                    "fold": fold,
                    "utc_hour": _utc_naive(h, fold),
                    "utc_offset_min": offset_min,
                    "local_hour_label": f"{h:%Y-%m-%d %H:00} {aware.tzname()}",
                    "kind": kind,
                }
            )
        h += timedelta(hours=1)
    return pl.DataFrame(
        rows,
        schema={
            "local_hour": pl.Datetime("us"),
            "fold": pl.Int8,
            "utc_hour": pl.Datetime("us"),
            "utc_offset_min": pl.Int16,
            "local_hour_label": pl.String,
            "kind": pl.String,
        },
    )


def nonexistent_hours(start_month: str, end_month: str) -> list[datetime]:
    cal = calendar(start_month, end_month)
    return cal.filter(pl.col("kind") == "nonexistent")["local_hour"].to_list()
