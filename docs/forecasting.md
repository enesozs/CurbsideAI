# Forecast Studio

Two forecasting tasks are evaluated out of sample with leakage tests. The headline result is in the tables below; the full per-fold and per-segment metrics are in `reports/forecast/`.

## Run it

```bash
uv run curbside forecast            # Task A rolling backtests (both sources) + Task B backtest + freeze live Task B
uv run curbside build-web           # includes web/public/data/forecast/
```

## Task A — day-ahead backtest

- **Issue and target:** issued 18:00 New York time on day D, for every UTC hour whose New York date is D+1 (23, 24 or 25 hours), for all 263 zones and each source separately. The lead time is 6–30 h.
- **Information:**
  - pickups in hours that ended before 18:00
  - calendar (hour, weekday, NY holidays, month, post-2025-01-05 regime flag)
  - Open-Meteo Previous Runs `*_previous_day2` weather: a value predicted at least 48 h before its valid time, so it is always older than the issue time
- **Framing:** "an operator with its own same-day trip feed". TLC's public data is about 2 months late.
- **Features:**
  - zone level: mean pickups/hour over the 28 days before issue (instead of a zone ID)
  - same-hour lags at 24 h (only when it ended before issue), 48 h, 168 h and 336 h
  - 4-week same-hour mean
  - pickups in the last 24 h and last 3 h
  - recent ratio (last 24 h ÷ same 24 h a week earlier)
  - calendar features
- **Leakage guard:** every panel read goes through `_lag`, which asserts the source hour is strictly before the issue hour. `tests/unit/test_forecast.py` builds a panel where y[t] = t and checks that every lag value is below the issue index.
- **Folds:** four quarterly test periods, 2025Q4 → 2026Q3. Each fold trains on up to 730 days of issue days that end 56 days before the test period. Days 56–28 before it are the early-stopping validation window; the last 28 days are the conformal calibration window. None of these windows overlap.
- **Models:**
  - baselines: same hour last week; 4-week same-hour mean; last same-hour value known at issue
  - Poisson GLM on log-lags plus hour/day dummies
  - histogram gradient boosting with Poisson loss (scikit-learn), without and with weather
- **Intervals:** split conformal on scaled residuals |y − ŷ| / √(ŷ+1), with one quantile per zone-volume segment, giving a 90% level.
- **Scoring:**
  - DST-split hours are excluded
  - metrics are reported per fold, per segment (high / mid / low terciles of zones with ≥ 0.5 pickups/hour; sparse below that) and per source
  - metrics: MAE, RMSE, WAPE, bias, peak-hour MAE, empirical coverage and width

Mean over the four quarterly folds (2025Q4 → 2026Q3). WAPE is citywide; sparse zones are scored by MAE.

**Yellow taxi**

| Model | WAPE | WAPE high | WAPE low | MAE sparse | Peak-hour MAE | 90% coverage |
|---|---|---|---|---|---|---|
| Gradient boosting | 20.2% | 17.8% | 94.5% | 0.202 | 4.70 | 90.6% |
| Gradient boosting + weather | 20.6% | 18.2% | 95.1% | 0.203 | 4.78 | 90.5% |
| Poisson GLM | 23.5% | 20.6% | 109.6% | 0.465 | 5.47 | — |
| 4-week same-hour mean | 23.7% | 21.3% | 96.5% | 0.191 | 5.64 | — |
| Same hour last week | 26.6% | 23.7% | 108.1% | 0.199 | 6.28 | — |
| Last same hour known | 32.0% | 28.9% | 106.3% | 0.200 | 6.57 | — |

Gradient boosting cuts WAPE by **15%** relative to the best baseline. It wins in every fold.

**Uber/Lyft**

| Model | WAPE | WAPE high | WAPE low | MAE sparse | Peak-hour MAE | 90% coverage |
|---|---|---|---|---|---|---|
| Gradient boosting | 13.7% | 13.1% | 19.1% | 0.045 | 17.14 | 89.9% |
| Gradient boosting + weather | 13.8% | 13.2% | 19.1% | 0.045 | 17.38 | 90.1% |
| Poisson GLM | 15.6% | 15.2% | 20.8% | 0.672 | 19.95 | — |
| 4-week same-hour mean | 16.1% | 15.6% | 21.4% | 0.039 | 20.66 | — |
| Same hour last week | 18.5% | 17.8% | 25.0% | 0.039 | 23.50 | — |
| Last same hour known | 24.3% | 24.4% | 28.6% | 0.040 | 28.08 | — |

Gradient boosting cuts WAPE by **15%** relative to the best baseline. It wins in every fold.

**What the results say:**
- **Gradient boosting wins clearly** for busy zones, and in every fold for both sources.
- **Weather forecasts add nothing measurable.** 48-hour-old forecasts at a single point don't help day-ahead pickup forecasts beyond what recent demand already carries. This is an honest null result.
- **Sparse zones (under 0.5 pickups/hour):** the model is no better than, and slightly worse than, the 4-week mean. Those zones are effectively baseline territory.
- **Intervals:** split-conformal 90% intervals reach 88–92% empirical coverage per fold.
- **Hardest quarter:** 2026Q1 (January–March), with holidays and weather disruptions.

Per-fold, per-segment metrics: `reports/forecast/task_a_metrics.json`.

## Task B — lag-aware monthly forecasts (live track record)

- **When and what:** at each TLC release, forecast every hour of the next unpublished month from public data only. The forecast is frozen with SHA-256 in `forecasts/task_b/<month>/` (committed) and scored by `score_frozen_task_b` once TLC publishes that month. Scoring verifies the hash first.
- **Method:** each zone's average week over the last 8 weeks, with holidays using the Sunday profile. Optionally it is scaled by last year's citywide ratio between the target month and the 8 weeks before it. The variant with the lower backtest WAPE is the one frozen.
- **Intervals:** conformal, from the 12-month backtest residuals per segment.

Backtest over the last 12 published months, each forecast from data up to the month before.

| Source | 8-week profile | Profile, seasonally adjusted | Same week last year | Frozen method |
|---|---|---|---|---|
| Yellow taxi | 23.8% | 23.3% | 25.5% | profile_8w_seasonal |
| Uber/Lyft | 15.7% | 15.5% | 16.8% | profile_8w_seasonal |

**Live:** October 2026 was frozen on 2026-11-14, with data through September 2026. Hashes: yellow `7cdd47e546a1298d…`, Uber/Lyft `b877a4bfed6d8eb8…`. It will be scored when TLC publishes October, expected around mid-December 2026.

## Decisions and deviations from the plan

- **scikit-learn instead of LightGBM.** LightGBM's macOS wheel needs a system OpenMP library (`libomp`) that this environment does not have. `HistGradientBoostingRegressor` is the same algorithm family, with a Poisson loss and temporal early stopping (`X_val`/`y_val`).
- **No zone ID feature.** HGB caps categorical cardinality at 255, below our 263 zones. A leakage-safe zone-level feature (28-day mean before issue) replaces it and should generalise better.
- **No statsforecast ETS/MSTL baseline.** The 4-week same-hour mean is a strong, transparent seasonal baseline at this granularity. Fitting MSTL on 526 hourly series for every rolling origin was not worth the cost.
- **Weather is the GFS-seamless Previous Runs archive.** Temperature covers 2023+; precipitation only from about 2024-01-20. Missing values stay null, never filled with observations.
