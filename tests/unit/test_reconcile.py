"""Reconciliation picks the counting convention that reproduces the aggregate (SYNTHETIC data)."""

from datetime import datetime as dt
from datetime import timedelta

import duckdb

from curbside import config, reconcile


def _silver(path, rows):
    con = duckdb.connect()
    con.execute(
        """CREATE TABLE s (source_month VARCHAR, pickup_local_hour TIMESTAMP,
           pickup_zone_id SMALLINT, flags INTEGER, vendor_id SMALLINT)"""
    )
    con.executemany("INSERT INTO s VALUES (?, ?, ?, ?, 2)", rows)
    con.execute(f"COPY s TO '{path}' (FORMAT parquet)")
    con.close()


def test_best_variant_excludes_negative_rows(tmp_path):
    h = dt(2024, 11, 10, 9)
    rows = [("2024-11", h + timedelta(minutes=i), 1, 0) for i in range(100)]
    rows += [("2024-11", h, 1, config.FLAG_NEGATIVE_AMOUNT)] * 5  # reversal rows
    rows += [("2024-11", dt(2024, 10, 31, 23), 2, config.FLAG_OUTSIDE_FILE_MONTH)]
    silver = tmp_path / "s.parquet"
    _silver(silver, rows)
    agg = tmp_path / "agg.csv"
    agg.write_text(
        "metric_month,industry,locationid,trip_count\n"
        "2024-11-01,Yellow Taxi,1,100\n"
        "2024-10-01,Yellow Taxi,2,1\n"
    )
    r = reconcile.reconcile("yellow", ["2024-10", "2024-11"], silver_glob=silver.as_posix(), agg_csv=agg)
    assert r["best_variant"] == "excl_negative_amount_pickup_month"
    best = r["variants"]["excl_negative_amount_pickup_month"]
    assert best["all_months_within_tolerance"] and best["max_abs_rel_diff"] == 0
    assert r["variants"]["all_rows_file_month"]["all_months_within_tolerance"] is False
    assert [m["status"] for m in best["per_month"]] == ["match", "match"]


def test_month_status_bands():
    assert reconcile.month_status(0.004) == "match"
    assert reconcile.month_status(-0.015) == "near"
    assert reconcile.month_status(0.09) == "mismatch"
    assert reconcile.month_status(None) == "no_aggregate"


def test_aggregate_url_filters_pickups_and_window():
    url = reconcile.aggregate_url("2023-01", "2026-09")
    assert "c5iv-bn4s.csv" in url and "Pick-up" in url and "2026-09-01" in url
