"""Forecasting: Task A day-ahead backtests and Task B lag-aware monthly forecasts.

Task A (docs/forecasting.md): issued 18:00 New York time on local day D for every UTC hour whose local
date is D+1, for every zone and source. Information: pickups in hours that ended before the
issue time, calendar, and vintage-safe weather (Previous Runs ``_previous_day2``).
Framed as "an operator with its own same-day trip feed" — TLC itself publishes ~2 months late.

Task B: at each TLC release, forecast the next unpublished month from data public at issue.

Every feature that reads the panel goes through ``_lag`` which asserts the source hour is strictly
before the issue hour (leakage is a release blocker).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import holidays as holidays_lib
import numpy as np
import polars as pl

from curbside import config, hours

ALPHA = 0.1  # 90% prediction intervals
PEAK_HOURS = {7, 8, 9, 16, 17, 18, 19}
POLICY_START = date(2025, 1, 5)  # CBD congestion pricing start: regime flag only, no causal use
FEATURE_VERSION = "a1"
TASK_A_ISSUE_HOUR = 18

BASE_FEATURES = [
    "level28d",
    "hour",
    "dow",
    "holiday",
    "month",
    "regime",
    "lead_h",
    "lag24",
    "lag48",
    "lag168",
    "lag336",
    "mean4w",
    "last24",
    "last3",
    "recent_ratio",
]
WEATHER_FEATURES = ["temp_c", "precip_mm"]
GRAPH_FEATURES = ["nbr_last24", "nbr_lag168", "nbr_level28d"]  # graph ablation: adjacency-weighted means


@dataclass
class Panel:
    """Dense [hour, zone] counts for one source, contiguous UTC hours."""

    source: str
    start_utc: datetime
    y: np.ndarray  # float32 [T, Z]; NaN = missing
    scoreable: np.ndarray  # bool [T]; False for DST-split hours
    zone_ids: list[int]
    local_date: np.ndarray  # datetime64[D] [T]
    local_hour: np.ndarray  # int8 [T]
    dow: np.ndarray  # int8 [T], Monday = 0
    utc: np.ndarray  # datetime64[us] [T]
    cumsum: np.ndarray = field(init=False)  # float64 [T+1, Z] with NaN as 0

    def __post_init__(self) -> None:
        filled = np.nan_to_num(self.y, nan=0.0).astype(np.float64)
        self.cumsum = np.vstack([np.zeros((1, filled.shape[1])), np.cumsum(filled, axis=0)])

    @property
    def T(self) -> int:
        return self.y.shape[0]

    @property
    def Z(self) -> int:
        return self.y.shape[1]

    def index_of(self, utc: datetime) -> int:
        return int((utc - self.start_utc).total_seconds() // 3600)


def load_panel(source: config.TripSource, months: list[str]) -> Panel:
    paths = [config.gold_zone_hour_path(source, m).as_posix() for m in months]
    con = config.db()
    df = con.sql(
        f"""SELECT utc_hour, utc_offset_min, pickup_zone_id, trip_count, data_quality_status
            FROM read_parquet({paths}) ORDER BY utc_hour, pickup_zone_id"""
    ).pl()
    con.close()
    zone_ids = sorted(df["pickup_zone_id"].unique().to_list())
    Z = len(zone_ids)
    T = df.height // Z
    if T * Z != df.height:
        raise RuntimeError("gold panel is not dense")
    utc = df["utc_hour"].to_numpy()[::Z]
    if not (np.diff(utc).astype("timedelta64[h]").astype(int) == 1).all():
        raise RuntimeError("gold panel UTC hours are not contiguous")
    y = df["trip_count"].to_numpy().reshape(T, Z).astype(np.float32)
    offs = df["utc_offset_min"].to_numpy()[::Z].astype("timedelta64[m]")
    local = utc + offs
    status = df["data_quality_status"].to_numpy()[::Z]
    return Panel(
        source=source,
        start_utc=utc[0].astype("datetime64[us]").astype(datetime),
        y=y,
        scoreable=status != "dst_ambiguous_split",
        zone_ids=zone_ids,
        local_date=local.astype("datetime64[D]"),
        local_hour=(local.astype("datetime64[h]") - local.astype("datetime64[D]"))
        .astype(int)
        .astype(np.int8),
        dow=((local.astype("datetime64[D]").astype(int) + 3) % 7).astype(np.int8),  # 1970-01-01 = Thursday
        utc=utc,
    )


# ---------------------------------------------------------------- Task A features


def issue_days(p: Panel, first: date, last: date) -> list[date]:
    """Local days D in [first, last] with an 18:00 issue hour and a complete D+1 in the panel."""
    out = []
    d = first
    days = set(p.local_date.astype(object))
    while d <= last:
        if d in days and (d + timedelta(days=1)) in days:
            out.append(d)
        d += timedelta(days=1)
    return out


def _hour_index(p: Panel, days: list[date]) -> tuple[np.ndarray, np.ndarray]:
    """(target index, issue index) for every target hour of every issue day."""
    ld = p.local_date.astype(object)
    by_day: dict[date, list[int]] = {}
    for i, d in enumerate(ld):
        by_day.setdefault(d, []).append(i)
    tgt, iss = [], []
    for d in days:
        issue = [i for i in by_day[d] if p.local_hour[i] == TASK_A_ISSUE_HOUR]
        if len(issue) != 1:
            raise RuntimeError(f"no unique 18:00 issue hour on {d}")
        for t in by_day[d + timedelta(days=1)]:
            tgt.append(t)
            iss.append(issue[0])
    return np.array(tgt), np.array(iss)


def _lag(p: Panel, tgt: np.ndarray, iss: np.ndarray, lag: int, allow_partial: bool = False) -> np.ndarray:
    """y[tgt - lag] per zone, NaN when the source hour is not strictly before the issue hour."""
    src = tgt - lag
    ok = (src >= 0) & (src < iss)
    if not allow_partial and not ok[src >= 0].all():
        raise AssertionError(f"lag {lag} would read hours at or after the issue time")
    out = np.full((len(tgt), p.Z), np.nan, dtype=np.float32)
    out[ok] = p.y[src[ok]]
    return out


def _window_sum(p: Panel, end: np.ndarray, width: int) -> np.ndarray:
    """Sum of y over [end - width, end) per zone (end exclusive)."""
    start = np.clip(end - width, 0, None)
    return (p.cumsum[end] - p.cumsum[start]).astype(np.float32)


def task_a_features(
    p: Panel, days: list[date], weather: pl.DataFrame | None = None, W: np.ndarray | None = None
) -> pl.DataFrame:
    tgt, iss = _hour_index(p, days)
    H, Z = len(tgt), p.Z
    ny_holidays = holidays_lib.country_holidays("US", subdiv="NY", years=range(2022, 2028))

    lag24 = _lag(p, tgt, iss, 24, allow_partial=True)  # NaN for target hours whose t-24 is after issue
    lag48 = _lag(p, tgt, iss, 48)
    lag168 = _lag(p, tgt, iss, 168)
    lag336 = _lag(p, tgt, iss, 336)
    lag504 = _lag(p, tgt, iss, 504)
    lag672 = _lag(p, tgt, iss, 672)
    with np.errstate(invalid="ignore"):
        mean4w = np.nanmean(np.stack([lag168, lag336, lag504, lag672]), axis=0)
    last24 = _window_sum(p, iss, 24)  # hours [iss-24, iss): all ended before 18:00
    level28d = _window_sum(p, iss, 672) / 672.0  # zone level: mean pickups/hour, 28 days before issue
    last3 = _window_sum(p, iss, 3)
    prev_week24 = _window_sum(p, iss - 168, 24)
    recent_ratio = (last24 + 1) / (prev_week24 + 1)

    graph: dict[str, np.ndarray] = {}
    if W is not None:  # row-normalised adjacency: mean over neighbouring zones (same issue-time info)
        graph = {
            "nbr_last24": last24 @ W.T,
            "nbr_lag168": np.nan_to_num(lag168) @ W.T,
            "nbr_level28d": level28d @ W.T,
        }
    tdate = p.local_date[tgt].astype(object)
    hour_level = {
        "hour": p.local_hour[tgt].astype(np.int16),
        "dow": p.dow[tgt].astype(np.int16),
        "holiday": np.array([d in ny_holidays for d in tdate], dtype=np.int8),
        "month": np.array([d.month for d in tdate], dtype=np.int8),
        "regime": np.array([d >= POLICY_START for d in tdate], dtype=np.int8),
        "lead_h": (tgt - iss).astype(np.int16),
        "target_idx": tgt.astype(np.int32),
        "issue_idx": iss.astype(np.int32),
    }
    cols: dict[str, np.ndarray] = {k: np.repeat(v, Z) for k, v in hour_level.items()}
    cols["zone"] = np.tile(np.arange(Z, dtype=np.int16), H)
    for name, arr in {
        "lag24": lag24,
        "lag48": lag48,
        "lag168": lag168,
        "lag336": lag336,
        "mean4w": mean4w,
        "last24": last24,
        "last3": last3,
        "recent_ratio": recent_ratio,
        "level28d": level28d,
        **graph,
    }.items():
        cols[name] = arr.ravel()
    cols["y"] = p.y[tgt].ravel()
    cols["scoreable"] = np.repeat(p.scoreable[tgt], Z)
    cols["valid_history"] = np.repeat(tgt - 672 >= 0, Z)
    df = pl.DataFrame(cols)
    if weather is not None:
        w = pl.DataFrame({"target_idx": np.arange(p.T, dtype=np.int32), "utc_hour": p.utc}).join(
            weather.with_columns(pl.col("utc_hour").cast(pl.Datetime("us"))), on="utc_hour", how="left"
        )
        df = df.join(w.select("target_idx", *WEATHER_FEATURES), on="target_idx", how="left")
    return df


def baselines(df: pl.DataFrame) -> pl.DataFrame:
    """Seasonal naive (1 week), 4-week same-hour mean, and last same-hour value known at issue."""
    return df.with_columns(
        pl.col("lag168").alias("b_snaive_1w"),
        pl.col("mean4w").alias("b_mean_4w"),
        pl.coalesce("lag24", "lag48").alias("b_last_same_hour"),
    )


# ---------------------------------------------------------------- models


def segments(p: Panel, train_end_idx: int, window: int = 24 * 56) -> np.ndarray:
    """Zone segment by mean hourly pickups over the 8 weeks before the fold: sparse/low/mid/high."""
    lo = max(0, train_end_idx - window)
    mean = np.nanmean(p.y[lo:train_end_idx], axis=0)
    seg = np.full(p.Z, "sparse", dtype=object)
    dense = mean >= 0.5
    if dense.any():
        q1, q2 = np.quantile(mean[dense], [1 / 3, 2 / 3])
        seg[dense & (mean < q1)] = "low"
        seg[dense & (mean >= q1) & (mean < q2)] = "mid"
        seg[dense & (mean >= q2)] = "high"
    return seg


def fit_gbm(train: pl.DataFrame, valid: pl.DataFrame, features: list[str], seed: int = 7):
    """Histogram gradient boosting with a Poisson loss, early-stopped on a later (temporal) window.

    scikit-learn's HistGradientBoostingRegressor (same algorithm family as LightGBM) is used because
    LightGBM's wheel needs a system OpenMP library that this environment does not provide.
    """
    from sklearn.ensemble import HistGradientBoostingRegressor

    model = HistGradientBoostingRegressor(
        loss="poisson",
        learning_rate=0.08,
        max_iter=1000,
        max_leaf_nodes=127,
        min_samples_leaf=200,
        l2_regularization=1.0,
        early_stopping=True,
        n_iter_no_change=30,
        random_state=seed,
    )
    model.fit(
        train.select(features).to_numpy(),
        train["y"].to_numpy(),
        X_val=valid.select(features).to_numpy(),
        y_val=valid["y"].to_numpy(),
    )
    return model


def fit_glm(train: pl.DataFrame, max_rows: int = 1_000_000, seed: int = 7):
    """Poisson GLM on log-lags + hour/day dummies (a transparent middle rung between baselines and GBMs)."""
    from sklearn.linear_model import PoissonRegressor

    sample = train.sample(n=min(max_rows, train.height), seed=seed)
    X, names = _glm_design(sample)
    model = PoissonRegressor(alpha=1e-4, max_iter=300)
    model.fit(X, sample["y"].to_numpy())
    return model, names


def _glm_design(df: pl.DataFrame) -> tuple[np.ndarray, list[str]]:
    num = df.select(
        *(
            pl.col(c).fill_null(pl.col("mean4w")).fill_nan(None).fill_null(0).log1p().alias(f"log_{c}")
            for c in ["lag24", "lag48", "lag168", "mean4w", "last24"]
        )
    ).to_numpy()
    hour = np.eye(24, dtype=np.float32)[df["hour"].to_numpy()]
    dow = np.eye(7, dtype=np.float32)[df["dow"].to_numpy()]
    other = df.select("holiday", "regime").to_numpy().astype(np.float32)
    names = [f"log_{c}" for c in ["lag24", "lag48", "lag168", "mean4w", "last24"]]
    names += [f"h{i}" for i in range(24)] + [f"d{i}" for i in range(7)] + ["holiday", "regime"]
    return np.hstack([np.nan_to_num(num), hour, dow, other]), names


def conformal_q(y: np.ndarray, pred: np.ndarray, seg: np.ndarray, alpha: float = ALPHA) -> dict[str, float]:
    """Split-conformal quantile of |y - ŷ| / sqrt(ŷ + 1) per segment (finite-sample corrected)."""
    s = np.abs(y - pred) / np.sqrt(pred + 1)
    out = {}
    for g in np.unique(seg):
        v = np.sort(s[seg == g])
        n = len(v)
        k = min(n - 1, int(np.ceil((n + 1) * (1 - alpha))) - 1)
        out[str(g)] = float(v[k]) if n else float("nan")
    return out


def apply_interval(pred: np.ndarray, seg: np.ndarray, q: dict[str, float]) -> tuple[np.ndarray, np.ndarray]:
    qs = np.array([q.get(str(g), np.nan) for g in seg])
    half = qs * np.sqrt(pred + 1)
    return np.clip(pred - half, 0, None), pred + half


def metrics(
    y: np.ndarray,
    pred: np.ndarray,
    hour: np.ndarray | None = None,
    lo: np.ndarray | None = None,
    hi: np.ndarray | None = None,
) -> dict[str, Any]:
    ok = ~np.isnan(pred) & ~np.isnan(y)
    y, pred = y[ok], pred[ok]
    err = pred - y
    out: dict[str, Any] = {
        "n": int(ok.sum()),
        "mae": float(np.mean(np.abs(err))) if len(y) else None,
        "rmse": float(np.sqrt(np.mean(err**2))) if len(y) else None,
        "wape": float(np.sum(np.abs(err)) / np.sum(y)) if np.sum(y) > 0 else None,
        "bias": float(np.mean(err)) if len(y) else None,
    }
    if hour is not None:
        pk = np.isin(hour[ok], list(PEAK_HOURS))
        out["peak_mae"] = float(np.mean(np.abs(err[pk]))) if pk.any() else None
    if lo is not None and hi is not None:
        lo_, hi_ = lo[ok], hi[ok]
        out["coverage"] = float(np.mean((y >= lo_) & (y <= hi_)))
        out["mean_width"] = float(np.mean(hi_ - lo_))
    return out


# ---------------------------------------------------------------- Task A backtest


@dataclass
class Fold:
    name: str
    test_start: date  # first target day
    test_end: date  # last target day (inclusive)


def default_folds() -> list[Fold]:
    q = [
        ("2025Q4", date(2025, 10, 1), date(2025, 12, 31)),
        ("2026Q1", date(2026, 1, 1), date(2026, 3, 31)),
        ("2026Q2", date(2026, 4, 1), date(2026, 6, 30)),
        ("2026Q3", date(2026, 7, 1), date(2026, 9, 30)),
    ]
    return [Fold(*f) for f in q]


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=config.ROOT,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def run_task_a(
    source: config.TripSource,
    months: list[str],
    folds: list[Fold] | None = None,
    weather: pl.DataFrame | None = None,
    train_days: int = 730,
    log=print,
    graph_W: np.ndarray | None = None,
    only: list[str] | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Rolling-origin Task A backtest. ``only`` restricts the GBM variants trained (e.g. the graph
    ``gbm_graph`` ablation) and skips GLM/baselines; ``write=False`` leaves saved forecasts alone."""
    folds = folds or default_folds()
    p = load_panel(source, months)
    out_dir = config.DATA / "forecasts" / "task_a" / source
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"source": source, "folds": {}, "feature_version": FEATURE_VERSION}
    first_day = p.local_date[0].astype(object)
    last_day = p.local_date[-1].astype(object)

    for fold in folds:
        # Issue day D forecasts D+1, so a target day T is issued on T-1.
        test_days = issue_days(p, fold.test_start - timedelta(days=1), fold.test_end - timedelta(days=1))
        calib_last = fold.test_start - timedelta(days=2)  # its targets end before the test period
        calib_days = issue_days(p, calib_last - timedelta(days=27), calib_last)
        valid_days = issue_days(p, calib_last - timedelta(days=55), calib_last - timedelta(days=28))
        train_days_ = issue_days(
            p, max(first_day, calib_last - timedelta(days=train_days)), calib_last - timedelta(days=56)
        )
        if not test_days or test_days[-1] > last_day:
            continue
        fs_idx = p.index_of(datetime.combine(calib_last, datetime.min.time()))
        seg = segments(p, fs_idx)

        def frame(days: list[date]) -> pl.DataFrame:
            f = task_a_features(p, days, weather, graph_W)
            f = f.filter(pl.col("valid_history") & pl.col("y").is_not_nan())
            return baselines(f)

        tr, va, ca, te = frame(train_days_), frame(valid_days), frame(calib_days), frame(test_days)
        tr = tr.filter(pl.col("scoreable"))
        va = va.filter(pl.col("scoreable"))
        log(
            f"{source} {fold.name}: train {tr.height:,} valid {va.height:,} "
            f"calib {ca.height:,} test {te.height:,}"
        )

        preds: dict[str, np.ndarray] = {}
        intervals: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        importances: dict[str, Any] = {}
        variants = {"gbm": BASE_FEATURES}
        if weather is not None:
            variants["gbm_weather"] = BASE_FEATURES + WEATHER_FEATURES
        if graph_W is not None:
            variants["gbm_graph"] = BASE_FEATURES + GRAPH_FEATURES
        if only:
            variants = {k: v for k, v in variants.items() if k in only}
        for name, feats in variants.items():
            model = fit_gbm(tr, va, feats)
            ca_pred = model.predict(ca.select(feats).to_numpy())
            q = conformal_q(ca["y"].to_numpy(), ca_pred, seg[ca["zone"].to_numpy()])
            te_pred = model.predict(te.select(feats).to_numpy())
            preds[name] = te_pred
            intervals[name] = apply_interval(te_pred, seg[te["zone"].to_numpy()], q)
            importances[name] = {"iterations": int(model.n_iter_), "features": feats, "conformal_q": q}
        if not only:
            glm, _ = fit_glm(tr)
            preds["glm"] = glm.predict(_glm_design(te)[0])
        for b in ["b_snaive_1w", "b_mean_4w", "b_last_same_hour"]:
            preds[b] = te[b].to_numpy().astype(np.float64)

        y = te["y"].to_numpy()
        score = te["scoreable"].to_numpy()
        zseg = seg[te["zone"].to_numpy()]
        hour = te["hour"].to_numpy()
        fold_res: dict[str, Any] = {
            "test": [str(fold.test_start), str(fold.test_end)],
            "train_rows": tr.height,
            "models": {},
            "details": importances,
        }
        for name, pr in preds.items():
            lo, hi = intervals.get(name, (None, None))
            by_seg = {}
            for g in ["all", "high", "mid", "low", "sparse"]:
                m = score & ((zseg == g) if g != "all" else True)
                by_seg[g] = metrics(
                    y[m], pr[m], hour[m], None if lo is None else lo[m], None if hi is None else hi[m]
                )
            fold_res["models"][name] = by_seg
        results["folds"][fold.name] = fold_res
        if not write:
            continue

        # Displayed model, pre-specified before results: GBM with weather when weather is supplied.
        best = "gbm_weather" if "gbm_weather" in preds else "gbm"
        lo, hi = intervals[best]
        issued_utc = p.utc[te["issue_idx"].to_numpy()]
        target_utc = p.utc[te["target_idx"].to_numpy()]
        out = pl.DataFrame(
            {
                "task": "A_day_ahead",
                "issued_at_utc": issued_utc,
                "information_cutoff_utc": issued_utc,
                "target_hour_utc": target_utc,
                "zone_id": np.array(p.zone_ids, dtype=np.int16)[te["zone"].to_numpy()],
                "trip_source": source,
                "model_id": best,
                "feature_version": FEATURE_VERSION,
                "git_sha": git_sha(),
                "point_forecast": preds[best].astype(np.float32),
                "lower": lo.astype(np.float32),
                "upper": hi.astype(np.float32),
                "interval_level": 1 - ALPHA,
                "evaluation_fold": fold.name,
                "y": y,
                "segment": zseg.astype(str),
                "scoreable": score,
                "b_snaive_1w": preds["b_snaive_1w"].astype(np.float32),
                "b_mean_4w": preds["b_mean_4w"].astype(np.float32),
                "glm": preds["glm"].astype(np.float32),
            }
        )
        out.write_parquet(out_dir / f"fold={fold.name}.parquet", compression="zstd")
    return results


# ---------------------------------------------------------------- Task B (lag-aware monthly)


def profile_forecast(p: Panel, target_month: str, info_end_idx: int, weeks: int = 8) -> dict[str, Any]:
    """Hour-of-week mean over the last ``weeks`` weeks before the cutoff; holidays use the Sunday profile."""
    lo = max(0, info_end_idx - weeks * 168)
    hist = p.y[lo:info_end_idx]
    how = p.dow[lo:info_end_idx].astype(int) * 24 + p.local_hour[lo:info_end_idx].astype(int)
    prof = np.full((168, p.Z), np.nan, dtype=np.float64)
    for k in range(168):
        sel = how == k
        if sel.any():
            prof[k] = np.nanmean(hist[sel], axis=0)
    cal = hours.calendar(target_month, target_month).filter(pl.col("kind") != "nonexistent").sort("utc_hour")
    local = cal["local_hour"].to_numpy()
    ld = local.astype("datetime64[D]")
    lh = (local.astype("datetime64[h]") - ld).astype(int)
    dow = (ld.astype(int) + 3) % 7
    ny = holidays_lib.country_holidays("US", subdiv="NY", years=range(2022, 2028))
    hol = np.array([d in ny for d in ld.astype(object)])
    dow_eff = np.where(hol, 6, dow)
    pred = prof[dow_eff * 24 + lh]
    # Seasonal adjustment: last year's citywide ratio (target month ÷ the 8 weeks before it), shifted
    # 364 days so weekdays align. Uses only data a year old, so it is known at issue time.
    first_utc = cal["utc_hour"].to_numpy()[0].astype("datetime64[us]").astype(datetime)
    ly_start = p.index_of(first_utc - timedelta(days=364))
    ly_end = ly_start + len(cal)
    ratio = float("nan")
    if ly_start - weeks * 168 >= 0 and ly_end <= info_end_idx:
        month_rate = np.nansum(p.y[ly_start:ly_end]) / (ly_end - ly_start)
        before_rate = np.nansum(p.y[ly_start - weeks * 168 : ly_start]) / (weeks * 168)
        ratio = month_rate / before_rate if before_rate > 0 else float("nan")
    return {
        "utc": cal["utc_hour"].to_numpy(),
        "pred": pred,
        "pred_seasonal": pred * ratio if np.isfinite(ratio) else np.full_like(pred, np.nan),
        "seasonal_ratio": ratio,
        "ambiguous": (cal["kind"] == "ambiguous").to_numpy(),
    }


def run_task_b_backtest(
    source: config.TripSource, months: list[str], test_months: list[str]
) -> dict[str, Any]:
    p = load_panel(source, months)
    res: dict[str, Any] = {"source": source, "months": {}}
    all_scaled: dict[str, list[np.ndarray]] = {}
    seg_last = None
    for tm in test_months:
        # information cutoff: first UTC hour whose New York date is in the target month
        start = int(np.argmax(p.local_date >= np.datetime64(f"{tm}-01")))
        fc = profile_forecast(p, tm, start)
        idx = np.array([p.index_of(u.astype("datetime64[us]").astype(datetime)) for u in fc["utc"]])
        y = p.y[idx]
        snaive = p.y[idx - 364 * 24] if (idx - 364 * 24 >= 0).all() else np.full_like(y, np.nan)
        seg = segments(p, start)
        seg_last = seg
        score = ~fc["ambiguous"][:, None] & np.ones_like(y, dtype=bool)
        zs = np.broadcast_to(seg, y.shape)
        out = {}
        cands = {"profile_8w": fc["pred"], "profile_8w_seasonal": fc["pred_seasonal"], "snaive_52w": snaive}
        for name, pr in cands.items():
            out[name] = {
                g: metrics(
                    y[score & ((zs == g) if g != "all" else True)],
                    pr[score & ((zs == g) if g != "all" else True)],
                )
                for g in ["all", "high", "mid", "low", "sparse"]
            }
        res["months"][tm] = out
        out["profile_8w_seasonal"]["seasonal_ratio"] = fc["seasonal_ratio"]
        for method in ("profile_8w", "profile_8w_seasonal"):
            pr = cands[method]
            s = np.abs(y - pr) / np.sqrt(np.nan_to_num(pr) + 1)
            for g in np.unique(seg):
                all_scaled.setdefault(f"{method}|{g}", []).append(s[:, seg == g][score[:, seg == g]].ravel())
    mean_wape = {
        m: float(
            np.mean([v[m]["all"]["wape"] for v in res["months"].values() if v[m]["all"]["wape"] is not None])
        )
        for m in ("profile_8w", "profile_8w_seasonal", "snaive_52w")
    }
    best = min(("profile_8w", "profile_8w_seasonal"), key=lambda m: mean_wape[m])
    q = {}
    for key, chunks in all_scaled.items():
        method, g = key.split("|")
        if method != best:
            continue
        v = np.sort(np.concatenate(chunks))
        v = v[~np.isnan(v)]
        k = min(len(v) - 1, int(np.ceil((len(v) + 1) * (1 - ALPHA))) - 1)
        q[g] = float(v[k])
    res["mean_wape"] = mean_wape
    res["best_method"] = best
    res["conformal_q"] = q
    res["segments"] = dict(zip(map(int, p.zone_ids), map(str, seg_last), strict=True))
    return res


def freeze_task_b(
    source: config.TripSource,
    months: list[str],
    target_month: str,
    conformal: dict[str, float],
    method: str = "profile_8w",
    out_root: Path | None = None,
) -> dict[str, Any]:
    """Issue and freeze the live Task B forecast for an unpublished month (committed to git)."""
    p = load_panel(source, months)
    out_root = out_root or (config.ROOT / "forecasts" / "task_b" / target_month)
    out_root.mkdir(parents=True, exist_ok=True)
    fc = profile_forecast(p, target_month, p.T)
    seg = segments(p, p.T)
    pred = fc["pred_seasonal"] if method == "profile_8w_seasonal" else fc["pred"]
    if np.isnan(pred).all():
        raise RuntimeError(f"{method} unavailable for {target_month}")
    zs = np.broadcast_to(seg, pred.shape)
    lo, hi = apply_interval(pred.ravel(), zs.ravel(), conformal)
    T, Z = pred.shape
    issued = datetime.now(UTC).replace(microsecond=0, tzinfo=None)
    df = pl.DataFrame(
        {
            "task": "B_lag_aware",
            "issued_at_utc": [issued] * (T * Z),
            "information_cutoff_utc": [
                p.utc[-1].astype("datetime64[us]").astype(datetime) + timedelta(hours=1)
            ]
            * (T * Z),
            "target_hour_utc": np.repeat(fc["utc"], Z),
            "zone_id": np.tile(np.array(p.zone_ids, dtype=np.int16), T),
            "trip_source": source,
            "model_id": method,
            "feature_version": FEATURE_VERSION,
            "git_sha": git_sha(),
            "point_forecast": np.round(pred.ravel(), 2).astype(np.float32),
            "lower": np.round(lo, 2).astype(np.float32),
            "upper": np.round(hi, 2).astype(np.float32),
            "interval_level": 1 - ALPHA,
            "segment": zs.ravel().astype(str),
        }
    )
    path = out_root / f"{source}.parquet"
    df.write_parquet(path, compression="zstd")
    from curbside.fetch import sha256_file

    manifest = {
        "task": "B_lag_aware",
        "trip_source": source,
        "target_month": target_month,
        "issued_at_utc": issued.isoformat() + "Z",
        "information_cutoff_utc": df["information_cutoff_utc"][0].isoformat() + "Z",
        "model_id": method,
        "seasonal_ratio": None if not np.isfinite(fc["seasonal_ratio"]) else float(fc["seasonal_ratio"]),
        "file": path.name,
        "sha256": sha256_file(path),
        "rows": df.height,
        "conformal_q": conformal,
        "git_sha": git_sha(),
    }
    (out_root / f"{source}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def score_frozen_task_b(source: config.TripSource, target_month: str) -> dict[str, Any] | None:
    """Score a frozen Task B forecast once its month is in gold (verifies the hash first)."""
    root = config.ROOT / "forecasts" / "task_b" / target_month
    man_path = root / f"{source}.manifest.json"
    gold_path = config.gold_zone_hour_path(source, target_month)
    if not man_path.exists() or not gold_path.exists():
        return None
    from curbside.fetch import sha256_file

    man = json.loads(man_path.read_text())
    if sha256_file(root / man["file"]) != man["sha256"]:
        raise RuntimeError(f"frozen forecast {target_month}/{source} was modified after issue")
    con = config.db()
    j = con.sql(
        f"""SELECT f.point_forecast, f.lower, f.upper, g.trip_count AS y, f.segment, g.data_quality_status
            FROM read_parquet('{(root / man["file"]).as_posix()}') f
            JOIN read_parquet('{gold_path.as_posix()}') g
              ON g.utc_hour = f.target_hour_utc AND g.pickup_zone_id = f.zone_id"""
    ).pl()
    con.close()
    j = j.filter(pl.col("data_quality_status") == "ok")
    y = j["y"].to_numpy().astype(float)
    return {
        g: metrics(
            y[m], j["point_forecast"].to_numpy()[m], lo=j["lower"].to_numpy()[m], hi=j["upper"].to_numpy()[m]
        )
        for g in ["all", "high", "mid", "low", "sparse"]
        for m in [np.ones(len(y), bool) if g == "all" else (j["segment"].to_numpy() == g)]
    }
