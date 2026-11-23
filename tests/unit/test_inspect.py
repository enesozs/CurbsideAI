"""Inspection tests on SYNTHETIC fixtures that mimic the real TLC schemas (not real trips)."""

from datetime import datetime

import duckdb
import pytest

from curbside.inspect_source import dst_transitions, inspect_trip_file

LOOKUP = "LocationID,Borough,Zone,service_zone\n" + "\n".join(
    f"{i},Manhattan,Zone {i},Yellow Zone" for i in [1, 2, 3, 264, 265]
)


@pytest.fixture
def lookup(tmp_path):
    p = tmp_path / "lookup.csv"
    p.write_text(LOOKUP + "\n")
    return p


def _write_yellow(path, rows):
    con = duckdb.connect()
    con.execute(
        """CREATE TABLE t (tpep_pickup_datetime TIMESTAMP, tpep_dropoff_datetime TIMESTAMP,
           PULocationID INTEGER, DOLocationID INTEGER, trip_distance DOUBLE,
           passenger_count BIGINT)"""
    )
    con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?)", rows)
    con.execute(f"COPY t TO '{path}' (FORMAT parquet)")
    con.close()


def test_dst_transitions_2024():
    assert dst_transitions("2024-03") == [{"date": "2024-03-10", "kind": "spring_forward", "local_hour": 2}]
    assert dst_transitions("2024-11") == [{"date": "2024-11-03", "kind": "fall_back", "local_hour": 1}]
    assert dst_transitions("2024-07") == []


def test_inspect_yellow_counts_anomalies(tmp_path, lookup):
    d = datetime
    rows = [
        # normal trip
        (d(2024, 11, 5, 8), d(2024, 11, 5, 8, 20), 1, 2, 1.5, 1),
        # outside nominal month (before / after)
        (d(2024, 10, 31, 23), d(2024, 11, 1, 0, 10), 1, 2, 2.0, 1),
        (d(2024, 12, 1, 0, 5), d(2024, 12, 1, 0, 30), 2, 1, 2.0, None),
        # dropoff before pickup, zero distance
        (d(2024, 11, 6, 9), d(2024, 11, 6, 8, 50), 3, 3, 0.0, 1),
        # catch-all zones and an id not in the lookup
        (d(2024, 11, 7, 10), d(2024, 11, 7, 10, 30), 264, 265, 3.0, 1),
        (d(2024, 11, 7, 11), d(2024, 11, 7, 11, 30), 999, 1, 3.0, 1),
        # fall-back ambiguous wall-clock hour: 2 trips on the day, 1 on each reference Sunday
        (d(2024, 11, 3, 1, 10), d(2024, 11, 3, 1, 20), 1, 1, 1.0, 1),
        (d(2024, 11, 3, 1, 40), d(2024, 11, 3, 1, 50), 1, 1, 1.0, 1),
        (d(2024, 11, 10, 1, 10), d(2024, 11, 10, 1, 20), 1, 1, 1.0, 1),
        (d(2024, 11, 17, 1, 10), d(2024, 11, 17, 1, 20), 1, 1, 1.0, 1),
        (d(2024, 11, 24, 1, 10), d(2024, 11, 24, 1, 20), 1, 1, 1.0, 1),
    ]
    path = tmp_path / "yellow.parquet"
    _write_yellow(path, rows)

    r = inspect_trip_file("yellow", "2024-11", path, lookup)

    assert r["rows"] == len(rows)
    assert r["rows_pickup_before_month"] == 1 and r["rows_pickup_after_month"] == 1
    assert r["validity"]["dropoff_before_pickup"] == 1
    assert r["validity"]["trip_distance_non_positive"] == 1
    assert r["null_share"] == {"passenger_count": round(1 / len(rows), 6)}
    pu = r["zones"]["PULocationID"]
    assert pu["count_264"] == 1 and pu["not_in_lookup"] == {"999": 1}
    assert r["zones"]["DOLocationID"]["count_265"] == 1
    (dst,) = r["dst_evidence"]
    assert dst["pickups_on_transition_day"] == 2
    assert dst["ratio_vs_median_reference"] == 2.0
    assert sum(r["pickups_by_wall_clock_hour"]) == len(rows)


def test_inspect_rejects_missing_columns(tmp_path, lookup):
    path = tmp_path / "bad.parquet"
    duckdb.sql(f"COPY (SELECT 1 AS foo) TO '{path}' (FORMAT parquet)")
    with pytest.raises(KeyError):
        inspect_trip_file("yellow", "2024-11", path, lookup)
