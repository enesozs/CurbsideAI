"""Forecast leakage and interval tests on SYNTHETIC panels."""

from datetime import date, datetime

import numpy as np
import pytest

from curbside import forecast


def _panel(days: int = 60, zones: int = 3) -> forecast.Panel:
    start = np.datetime64("2024-01-01T05:00")  # local midnight EST
    T = days * 24
    utc = start + np.arange(T).astype("timedelta64[h]")
    local = utc - np.timedelta64(5, "h")  # January: no DST
    y = np.repeat(np.arange(T, dtype=np.float32)[:, None], zones, axis=1)  # y[t] = t
    return forecast.Panel(
        source="yellow",
        start_utc=datetime(2024, 1, 1, 5),
        y=y,
        scoreable=np.ones(T, bool),
        zone_ids=list(range(1, zones + 1)),
        local_date=local.astype("datetime64[D]"),
        local_hour=(local.astype("datetime64[h]") - local.astype("datetime64[D]"))
        .astype(int)
        .astype(np.int8),
        dow=((local.astype("datetime64[D]").astype(int) + 3) % 7).astype(np.int8),
        utc=utc,
    )


def test_features_never_read_at_or_after_issue():
    p = _panel()
    days = forecast.issue_days(p, date(2024, 1, 30), date(2024, 2, 20))
    df = forecast.task_a_features(p, days)
    iss = df["issue_idx"].to_numpy()
    for col in ["lag24", "lag48", "lag168", "lag336"]:
        v = df[col].to_numpy()
        ok = ~np.isnan(v)
        assert ok.any()
        assert (v[ok] < iss[ok]).all(), col  # value == source hour index
    # last24 = sum of the 24 hour indices just before the issue hour
    expected = np.array([sum(range(i - 24, i)) for i in iss], dtype=np.float64)
    assert np.allclose(df["last24"].to_numpy(), expected)
    # lag24 is only available for targets before 18:00 the next day
    late = df["hour"].to_numpy() >= forecast.TASK_A_ISSUE_HOUR
    assert np.isnan(df["lag24"].to_numpy()[late]).all()
    assert (df["lead_h"].to_numpy() >= 6).all() and (df["lead_h"].to_numpy() <= 29).all()


def test_short_lag_is_rejected():
    p = _panel()
    days = forecast.issue_days(p, date(2024, 1, 30), date(2024, 1, 31))
    tgt, iss = forecast._hour_index(p, days)
    with pytest.raises(AssertionError):
        forecast._lag(p, tgt, iss, 12)


def test_conformal_interval_covers_calibration_quantile():
    rng = np.random.default_rng(0)
    pred = rng.uniform(1, 50, 5000)
    y = rng.poisson(pred).astype(float)
    seg = np.array(["a"] * 5000)
    q = forecast.conformal_q(y, pred, seg, alpha=0.1)
    lo, hi = forecast.apply_interval(pred, seg, q)
    cov = np.mean((y >= lo) & (y <= hi))
    assert 0.89 <= cov <= 0.92
    assert (lo >= 0).all()


def test_metrics_wape_zero_denominator():
    m = forecast.metrics(np.zeros(4), np.ones(4))
    assert m["wape"] is None and m["mae"] == 1.0


def test_graph_features_are_neighbour_means_of_issue_time_info():
    p = _panel(zones=3)
    p.y[:, 1] *= 2  # zone 1 has twice the counts
    p.__post_init__()
    W = np.array([[0, 1, 0], [0.5, 0, 0.5], [0, 0, 0]], dtype=np.float32)  # zone 2 isolated
    days = forecast.issue_days(p, date(2024, 1, 30), date(2024, 1, 31))
    df = forecast.task_a_features(p, days, W=W)
    z = df["zone"].to_numpy()
    last24 = df["last24"].to_numpy()
    nbr = df["nbr_last24"].to_numpy()
    # zone 0's only neighbour is zone 1; zone 2 has none
    assert np.allclose(nbr[z == 0], last24[z == 1])
    assert np.allclose(nbr[z == 2], 0)
    assert np.allclose(nbr[z == 1], (last24[z == 0] + last24[z == 2]) / 2)
