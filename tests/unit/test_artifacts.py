"""Web artifacts round-trip gold values exactly (SYNTHETIC fixtures)."""

import json
from datetime import datetime as dt
from datetime import timedelta

import duckdb
import numpy as np
import pandas as pd

from curbside import artifacts, config, gold, silver

ZONES = [1, 2, 3]


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GOLD", tmp_path / "gold")
    monkeypatch.setattr(config, "SILVER", tmp_path / "silver")
    monkeypatch.setattr(config, "QA", tmp_path / "qa")
    zdir = tmp_path / "gold" / "zones"
    zdir.mkdir(parents=True)
    pd.DataFrame(
        {
            "LocationID": ZONES,
            "borough": ["Manhattan"] * 3,
            "zone": ["A", "B", "C"],
            "service_zone": ["Yellow Zone"] * 3,
            "area_km2": [1.0, 2.0, 0.5],
            "label_lon": [-74.0] * 3,
            "label_lat": [40.7] * 3,
        }
    ).to_parquet(zdir / "zones.parquet")
    (zdir / "zones_web.geojson").write_text('{"type":"FeatureCollection","features":[]}')


def _raw(path, source):
    con = duckdb.connect()
    base = dt(2024, 11, 3, 0, 30)
    if source == "yellow":
        con.execute(
            """CREATE TABLE t (tpep_pickup_datetime TIMESTAMP, tpep_dropoff_datetime TIMESTAMP,
               PULocationID INTEGER, DOLocationID INTEGER, trip_distance DOUBLE, total_amount DOUBLE)"""
        )
        rows = [
            (base + timedelta(hours=h), base + timedelta(hours=h, minutes=9), 1 + h % 3, 2, 1.0, 9.0)
            for h in range(40)
            for _ in range(h % 5)
        ]
        con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?)", rows)
    else:
        con.execute(
            """CREATE TABLE t (hvfhs_license_num VARCHAR, request_datetime TIMESTAMP,
               pickup_datetime TIMESTAMP, dropoff_datetime TIMESTAMP, PULocationID INTEGER,
               DOLocationID INTEGER, trip_miles DOUBLE, base_passenger_fare DOUBLE)"""
        )
        rows = [
            (
                "HV0003",
                base + timedelta(hours=h, minutes=-4),
                base + timedelta(hours=h),
                base + timedelta(hours=h, minutes=9),
                2,
                1,
                1.0,
                9.0,
            )
            for h in range(30)
            for _ in range(12)
        ]
        con.executemany("INSERT INTO t VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    con.execute(f"COPY t TO '{path}' (FORMAT parquet)")
    con.close()


def test_binaries_match_gold(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    for source in ("yellow", "hvfhv"):
        raw = tmp_path / f"{source}.parquet"
        _raw(raw, source)
        silver.build_silver(source, "2024-11", raw, ZONES, "sha")
        gold.build_zone_hour(source, "2024-11", ZONES)

    out = tmp_path / "web"
    res = artifacts.build(out=out, months=["2024-11"])
    assert res["months"] == 1
    meta = json.loads((out / "meta.json").read_text())
    idx = meta["sources"]["yellow"]["months"]["2024-11"]
    assert idx["hours"] == 30 * 24 + 1  # includes the repeated fall-back hour
    assert len(meta["dst_split_hours_utc"]) == 2

    for source in ("yellow", "hvfhv"):
        arr = np.fromfile(out / "zh" / source / "2024-11.bin", dtype="<u2").reshape(idx["hours"], len(ZONES))
        g = duckdb.execute(
            f"SELECT trip_count FROM read_parquet('{config.gold_zone_hour_path(source, '2024-11')}') "
            "ORDER BY utc_hour, pickup_zone_id"
        ).fetchnumpy()["trip_count"]
        assert np.array_equal(arr.ravel(), g)
        assert int(arr.sum()) == meta["sources"][source]["months"]["2024-11"]["total"]

    wait = np.fromfile(out / "wait" / "2024-11.bin", dtype="<u2").reshape(idx["hours"], len(ZONES))
    assert (wait[:, 0] == artifacts.SUPPRESSED).all()  # zone 1: no HVFHV trips
    assert set(wait[wait[:, 1] != artifacts.SUPPRESSED, 1].tolist()) == {240}  # 4-minute waits

    daily = np.fromfile(out / "daily" / "yellow.bin", dtype="<u4").reshape(30, len(ZONES))
    assert int(daily.sum()) == idx["total"]
