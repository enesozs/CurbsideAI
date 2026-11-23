"""The policy estimator recovers a known effect on a SYNTHETIC panel and its placebo finds none."""

import numpy as np
import polars as pl

from curbside import config, policy


def _panel(effect: float, drift_per_year: float = 0.0, seed: int = 1) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    months = config.month_range("2023-01", "2026-09")
    rows = []
    for grp, n_pairs in (("treated", 60), ("control", 80)):
        for k in range(n_pairs):
            level = rng.normal(5.8, 0.2)
            amp = 0.08 if grp == "treated" else 0.02  # group-specific seasonality
            for i, m in enumerate(months):
                moy = int(m[5:])
                season = amp * np.cos(2 * np.pi * (moy - 7) / 12)
                drift = drift_per_year * i / 12 if grp == "treated" else 0.0
                post = effect if (grp == "treated" and m >= "2025-01") else 0.0
                y = level + season + drift + np.log1p(post) + rng.normal(0, 0.02)
                rows.append(
                    {
                        "o": k,
                        "d": 1000 + k if grp == "treated" else 2000 + k,
                        "month": m,
                        "grp": grp,
                        "pace": float(np.exp(y)),
                        "n": 50,
                    }
                )
    return pl.DataFrame(rows)


def test_seasonal_did_recovers_effect_and_placebo_is_null():
    pan = _panel(effect=0.10)
    r = policy.did_seasonal(pan)
    assert abs(r["att_pct"] - 10.0) < 1.0
    assert r["ci_pct"][0] < 10.0 < r["ci_pct"][1]
    pl_ = policy.did_seasonal(pan, post_start="2024-01", end="2025-01", min_months=12)
    assert pl_["ci_pct"][0] <= 0 <= pl_["ci_pct"][1]


def test_naive_event_study_flags_seasonal_differences():
    r = policy.event_study(_panel(effect=0.0))
    assert r["pretrend_p"] < 0.05  # seasonality differs by group, so naive pre-trends fail


def test_placebo_detects_differential_drift():
    pan = _panel(effect=0.0, drift_per_year=0.03)
    pl_ = policy.did_seasonal(pan, post_start="2024-01", end="2025-01", min_months=12)
    assert pl_["ci_pct"][0] > 0  # drift shows up as a spurious placebo "effect"
    trend = policy.did_with_trend(pan)
    assert trend["ci_pct"][0] <= 0 <= trend["ci_pct"][1]  # the trend term absorbs it
