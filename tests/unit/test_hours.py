from datetime import datetime

import polars as pl

from curbside import hours


def test_classify_dst_hours_2024():
    assert hours.classify(datetime(2024, 3, 10, 2)) == "nonexistent"
    assert hours.classify(datetime(2024, 3, 10, 3)) == "normal"
    assert hours.classify(datetime(2024, 11, 3, 1)) == "ambiguous"
    assert hours.classify(datetime(2024, 11, 3, 2)) == "normal"
    assert hours.classify(datetime(2024, 7, 1, 12)) == "normal"


def test_calendar_row_counts_per_month():
    nov = hours.calendar("2024-11", "2024-11")
    assert nov.height == 30 * 24 + 1  # one extra row for the repeated hour
    mar = hours.calendar("2024-03", "2024-03")
    assert mar.filter(pl.col("kind") != "nonexistent").height == 31 * 24 - 1


def test_fall_back_maps_to_two_distinct_utc_hours():
    cal = hours.calendar("2024-11", "2024-11")
    amb = cal.filter(pl.col("kind") == "ambiguous").sort("fold")
    assert amb["utc_hour"].to_list() == [datetime(2024, 11, 3, 5), datetime(2024, 11, 3, 6)]
    assert amb["local_hour_label"].to_list() == ["2024-11-03 01:00 EDT", "2024-11-03 01:00 EST"]
    assert amb["utc_offset_min"].to_list() == [-240, -300]


def test_utc_hours_unique_and_contiguous_excluding_nonexistent():
    cal = hours.calendar("2024-01", "2024-12").filter(pl.col("kind") != "nonexistent")
    utc = cal["utc_hour"].sort()
    assert utc.n_unique() == cal.height
    diffs = utc.diff().drop_nulls().unique().to_list()
    assert len(diffs) == 1 and diffs[0].total_seconds() == 3600


def test_nonexistent_hours_listed():
    assert hours.nonexistent_hours("2024-01", "2024-12") == [datetime(2024, 3, 10, 2)]
