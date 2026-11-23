# Curbside AI — Technical report

*November 2026. Data: NYC TLC trip records, January 2023 – September 2026.*

## 1. Question and scope

Curbside asks three questions of public trip records:
1. **Where and when do trips start?** (description)
2. **Can completed pickups per taxi zone and hour be forecast better than seasonal baselines, honestly?** (the flagship)
3. **What changed for trips inside Manhattan's congestion toll zone after 2025-01-05?** (policy)

It covers two separate populations: yellow taxis (159 M trips) and high-volume for-hire services, meaning Uber/Lyft (905 M trips). They are never silently summed.

## 2. Data and its semantics

- **Licences:** checked before use, and recorded with each dataset. Two MTA datasets were checked and not adopted.
- **Timestamps are naive New York wall time.** The evidence: the skipped spring-forward hour has 0 pickups, and the repeated fall-back hour has 1.5–2.1× normal volume, across all eight transitions in the window.
  - An explicit hour calendar maps local hours to UTC.
  - The two real hours behind the repeated "1 AM" are split evenly, flagged, and excluded from scoring. That is the only honest option, because the records can't tell the two hours apart.
- **Nothing is dropped silently.** Silver keeps every raw row with 11 validation flags. The counting rules were selected by **reconciliation against TLC's own aggregate**, testing every flag combination:
  - Uber/Lyft: 44 of 45 months within 0.5%.
  - Yellow: median gap 0.41%, with month-specific residuals. These are consistent with TLC re-publishing files, so they are shown rather than adjusted.
- **Schema drift** is handled per file: 4 yellow and 2 HVFHV schema versions in the window.

## 3. Forecasting

- **Task A (day-ahead):** issued at 18:00 New York time for every hour of the next day, for 263 zones per source, using only data available before the issue time. Every feature read asserts that rule, and a test proves it.
- **Weather:** Open-Meteo *Previous Runs* `_previous_day2`, i.e. values forecast at least 48 h ahead. The "historical forecast" archive was rejected because it is stitched from the first hours of each run, which is close to observed weather and would leak.
- **Evaluation:** four quarterly rolling-origin folds (2025Q4 – 2026Q3). Each has disjoint training, early-stopping and conformal-calibration windows.

| Citywide WAPE (mean of 4 folds) | Yellow | Uber/Lyft |
|---|---|---|
| Best seasonal baseline (4-week same-hour mean) | 23.7% | 16.1% |
| Poisson GLM | 23.5% | 15.6% |
| Gradient boosting (Poisson) | **20.2%** | **13.7%** |
| + zone-network features | **19.8%** | **13.6%** |
| 90% interval coverage (split conformal) | 90.6% | 89.9% |

- Gradient boosting wins in every fold for both services.
- Weather forecasts add nothing measurable.
- Sparse zones (under 0.5 pickups/hour) stay baseline territory.
- **Task B (live, lag-aware):** the October 2026 hourly forecasts are frozen with SHA-256 and will be scored automatically when TLC publishes. The method (a seasonally adjusted 8-week profile) was chosen by a 12-month backtest: WAPE 23.3% for yellow and 15.5% for Uber/Lyft, against 25.5% and 16.8% for "same week last year".

## 4. Congestion pricing

- **Toll-zone membership:** 38 taxi zones, read from TLC's own fee field, whose incidence is bimodal.
- **Outcome:** log median trip pace per OD pair and month.
- **Estimator:** pair and month fixed effects, group-specific seasonality, standard errors clustered by pair. It was validated on synthetic panels with planted effects and planted drift.
- **Yellow taxis:** the 2024 placebo fails (+1.4%), because toll-zone trips were already slowing relative to the outer boroughs. **No causal claim.**
- **Uber/Lyft:** the placebo is negligible. Toll-zone trip pace changed by −0.3% (95% CI −0.4 to −0.2) relative to the outer boroughs; extrapolating the pre-2025 drift gives −3.3%. Against the rest of Manhattan, which may itself have been affected, it changed by +0.4%. **There was no large change either way.**
- **Limits:** pace is not traffic speed. Coincident changes (return to office, fares, roadworks, trip mix) aren't separated, and trips deterred by the toll are invisible in this data.

## 5. Product

A static web app with no server: about 160 MB of binary and JSON artifacts, aggregates only.
- **City Pulse:** a scrubbable map with fixed log classes and DST-correct New York time.
- **Flows**, **Forecasts**, **Congestion pricing**, **Data quality**, **Method.**
- **Quality gates:**
  - 27 Playwright checks: smoke tests on desktop and mobile (passing against both the dev server and the production build), plus the audits below
  - an axe-core WCAG 2.1 AA audit of 6 pages × 2 themes
  - a performance budget (map ready in about 0.6 s; about 30 ms per hour step)
  - 4 screenshot-regression baselines
  - 40 Python unit tests on synthetic fixtures
  - CI runs from a clean clone

## 6. Deviations from the original plan

- **HVFHV was added alongside yellow,** and the window moved to 2023+ from a 2024-only yellow plan.
- **"Next hour" forecasting was replaced** by a day-ahead backtest plus a lag-aware live track record.
- **scikit-learn HistGradientBoosting replaced LightGBM.** LightGBM's wheel needs a system OpenMP library this environment lacked.
- **Flows use MapLibre instead of deck.gl,** and there is **no FastAPI service.** Static artifacts are enough.
- **The policy study uses TLC data only.**

## 7. What's next

- Score the October 2026 Task B forecast when TLC publishes it (around mid-December), via `curbside refresh --apply`.
- Retrain Task A with graph features (`feature_version` b1) at the next refresh.
- Public deployment.
- Parked: GNN beyond the ablation, subway ridership, LLM research copilot.
