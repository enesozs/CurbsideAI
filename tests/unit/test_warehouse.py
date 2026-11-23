"""Silver → gold invariants on SYNTHETIC fixtures (TLC-shaped schemas, invented trips)."""

from datetime import datetime as dt

import duckdb
import pytest

from curbside import config, gold, silver

ZONES = [1, 2, 3]


def _yellow(path, rows):
    con = duckdb.connect()
    con.execute(
        """CREATE TABLE t (tpep_pickup_datetime TIMESTAMP, tpep_dropoff_datetime TIMESTAMP,
           PULocationID INTEGER, DOLocationID INTEGER, trip_distance DOUBLE, fare_amount DOUBLE,
           total_amount DOUBLE, payment_type BIGINT, Airport_fee DOUBLE)"""
    )
    con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)", rows)
    con.execute(f"COPY t TO '{path}' (FORMAT parquet)")
    con.close()


def _hvfhv(path, rows):
    con = duckdb.connect()
    con.execute(
        """CREATE TABLE t (hvfhs_license_num VARCHAR, request_datetime TIMESTAMP,
           pickup_datetime TIMESTAMP, dropoff_datetime TIMESTAMP, PULocationID INTEGER,
           DOLocationID INTEGER, trip_miles DOUBLE, base_passenger_fare DOUBLE)"""
    )
    con.executemany("INSERT INTO t VALUES ('HV0003', ?, ?, ?, ?, ?, ?, ?)", rows)
    con.execute(f"COPY t TO '{path}' (FORMAT parquet)")
    con.close()


def trip(pu, minutes=10, zone=1, dest=2, dist=1.0, total=10.0):
    from datetime import timedelta

    return (pu, pu + timedelta(minutes=minutes), zone, dest, dist, total - 2, total, 1)


@pytest.fixture
def nov_yellow(tmp_path):
    rows = [
        trip(dt(2024, 11, 5, 8, 10)),
        trip(dt(2024, 11, 5, 8, 40)),
        trip(dt(2024, 11, 5, 9, 5), zone=2, dest=265),
        # fall-back repeated hour: 3 trips -> split 2 (EDT) / 1 (EST)
        trip(dt(2024, 11, 3, 1, 5)),
        trip(dt(2024, 11, 3, 1, 25)),
        trip(dt(2024, 11, 3, 1, 55)),
        # flagged but counted: negative duration, zero distance
        trip(dt(2024, 11, 6, 10), minutes=-5),
        trip(dt(2024, 11, 6, 11), dist=0.0),
        # excluded from counts: negative amount, far outside month
        trip(dt(2024, 11, 7, 12), total=-10.0),
        trip(dt(2002, 12, 31, 22)),
        # catch-all / unknown pickup zones: in silver, not in gold
        trip(dt(2024, 11, 8, 13), zone=264),
        trip(dt(2024, 11, 8, 13), zone=999),
        # just outside the file month (belongs to Dec): not in Nov gold
        trip(dt(2024, 12, 1, 0, 30)),
    ]
    path = tmp_path / "yellow.parquet"
    _yellow(path, rows)
    return path, rows


def _silver(tmp_path, source, month, raw, monkeypatch):
    monkeypatch.setattr(config, "QA", tmp_path / "qa")
    out = tmp_path / "silver" / f"{source}_{month}.parquet"
    stats = silver.build_silver(source, month, raw, ZONES, "sha-test", out_path=out)
    return out, stats


def test_silver_keeps_every_row_and_flags(tmp_path, nov_yellow, monkeypatch):
    raw, rows = nov_yellow
    out, stats = _silver(tmp_path, "yellow", "2024-11", raw, monkeypatch)
    assert stats["raw_rows"] == stats["silver_rows"] == len(rows)
    fc = stats["flag_counts"]
    assert fc["negative_amount"] == 1
    assert fc["negative_duration"] == 1
    assert fc["non_positive_distance"] == 1
    assert fc["pickup_far_outside_file_month"] == 1
    assert fc["pickup_outside_file_month"] == 1
    assert fc["catchall_pickup_zone"] == 1
    assert fc["unknown_zone"] == 1
    assert fc["wait_invalid"] == 0  # yellow has no wait time
    assert stats["counted_rows"] == len(rows) - 2


def test_gold_split_zero_fill_and_reconcile(tmp_path, nov_yellow, monkeypatch):
    raw, _ = nov_yellow
    s_path, _ = _silver(tmp_path, "yellow", "2024-11", raw, monkeypatch)
    out = tmp_path / "gold.parquet"
    res = gold.build_zone_hour("yellow", "2024-11", ZONES, out_path=out, silver_paths=[s_path])

    assert res["gold_rows"] == (30 * 24 + 1) * len(ZONES)
    assert res["keys_unique"] and res["totals_reconcile"]
    # 3 + 2 (flagged-but-counted) + 3 (DST) = 8 trips in zones 1-3 during November
    assert res["gold_trip_total"] == 8
    assert res["silver_counted_catchall_pickup"] == 1
    assert res["silver_counted_unknown_pickup"] == 1

    duckdb.execute(f"CREATE OR REPLACE VIEW g AS SELECT * FROM read_parquet('{out}')")
    split = duckdb.execute(
        "SELECT fold, trip_count, data_quality_status FROM g WHERE pickup_zone_id = 1 "
        "AND local_hour_label LIKE '2024-11-03 01:00%' ORDER BY fold"
    ).fetchall()
    assert split == [(0, 2, "dst_ambiguous_split"), (1, 1, "dst_ambiguous_split")]
    zero = duckdb.execute(
        "SELECT trip_count FROM g WHERE pickup_zone_id = 3 AND utc_hour = TIMESTAMP '2024-11-20 12:00'"
    ).fetchone()
    assert zero == (0,)
    assert duckdb.execute("SELECT min(trip_count) FROM g").fetchone()[0] >= 0
    # 08:10 and 08:40 local on Nov 5 (EST, UTC-5) -> 13:00 UTC
    assert duckdb.execute(
        "SELECT trip_count FROM g WHERE pickup_zone_id = 1 AND utc_hour = TIMESTAMP '2024-11-05 13:00'"
    ).fetchone() == (2,)


def test_gold_od_keeps_outside_nyc_destination(tmp_path, nov_yellow, monkeypatch):
    raw, _ = nov_yellow
    s_path, _ = _silver(tmp_path, "yellow", "2024-11", raw, monkeypatch)
    out = tmp_path / "od.parquet"
    res = gold.build_od_daily("yellow", "2024-11", ZONES, out_path=out, silver_paths=[s_path])
    assert res["od_trip_total"] == 8
    dest = duckdb.execute(
        f"SELECT DISTINCT destination_zone_id FROM read_parquet('{out}') ORDER BY 1"
    ).fetchall()
    assert dest == [(2,), (265,)]
    # flagged rows count as trips but are excluded from duration statistics
    nd = duckdb.execute(
        f"SELECT trip_count, stats_n FROM read_parquet('{out}') WHERE local_date = DATE '2024-11-06'"
    ).fetchall()
    assert nd == [(2, 0)]


def test_spring_forward_rows_move_to_next_hour(tmp_path, monkeypatch):
    raw = tmp_path / "mar.parquet"
    _yellow(raw, [trip(dt(2024, 3, 10, 2, 30)), trip(dt(2024, 3, 10, 3, 15))])
    s_path, stats = _silver(tmp_path, "yellow", "2024-03", raw, monkeypatch)
    assert stats["flag_counts"]["dst_nonexistent"] == 1
    out = tmp_path / "g.parquet"
    res = gold.build_zone_hour("yellow", "2024-03", ZONES, out_path=out, silver_paths=[s_path])
    assert res["totals_reconcile"] and res["gold_rows"] == (31 * 24 - 1) * len(ZONES)
    # local 03:00 EDT = 07:00 UTC holds both trips
    assert duckdb.execute(
        f"SELECT trip_count FROM read_parquet('{out}') WHERE pickup_zone_id = 1 "
        "AND utc_hour = TIMESTAMP '2024-03-10 07:00'"
    ).fetchone() == (2,)


def test_hvfhv_wait_time_rules(tmp_path, monkeypatch):
    from datetime import timedelta

    base = dt(2024, 11, 12, 18, 0)
    rows = []
    for i in range(12):  # valid waits 60..720 s
        pu = base + timedelta(minutes=i * 4)
        rows.append((pu - timedelta(seconds=60 * (i + 1)), pu, pu + timedelta(minutes=9), 1, 2, 1.0, 9.0))
    rows.append((base + timedelta(minutes=1), base, base + timedelta(minutes=5), 1, 2, 1.0, 9.0))  # negative
    rows.append((base - timedelta(hours=2), base, base + timedelta(minutes=5), 1, 2, 1.0, 9.0))  # > 1 h
    raw = tmp_path / "hv.parquet"
    _hvfhv(raw, rows)
    s_path, stats = _silver(tmp_path, "hvfhv", "2024-11", raw, monkeypatch)
    assert stats["flag_counts"]["wait_invalid"] == 2
    out = tmp_path / "g.parquet"
    gold.build_zone_hour("hvfhv", "2024-11", ZONES, out_path=out, silver_paths=[s_path])
    count, med, n = duckdb.execute(
        f"SELECT trip_count, median_wait_s, wait_n FROM read_parquet('{out}') "
        "WHERE pickup_zone_id = 1 AND utc_hour = TIMESTAMP '2024-11-12 23:00'"
    ).fetchone()
    assert count == 14 and n == 12
    assert med == pytest.approx(390.0)  # median of 60..720 step 60
