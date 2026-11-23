# Congestion-pricing study (TLC data only)

**Question:** after the Central Business District tolling program began on **2025-01-05**, did taxi and Uber/Lyft trips inside the Congestion Relief Zone (CRZ) get faster or slower *relative to comparable trips elsewhere in the city*?

**Scope:** TLC trip data only. The MTA vehicle-entry and CBD-speed datasets were licence-checked and offered, but not adopted. This study therefore measures **taxi/for-hire trip pace**, which is a proxy for speed on the routes these trips take, not all road traffic.

Run it with `uv run curbside policy`. Results go to `reports/policy/policy_study.json` and the app's Policy page.

## Design

| Element | Choice |
|---|---|
| CRZ zones | Derived from TLC's own `cbd_congestion_fee`: a zone is inside if at least 60% of its intra-zone trips in 2025+ paid the fee. Fee incidence is bimodal: CRZ zones score 0.72–1.0, with the lowest along the toll-exempt FDR / West Side Highway. Zones straddling 60th St score ≤ 0.45. Result: **38 zones**, all in Manhattan at or below 60th St. |
| Outcome | log of median trip pace (seconds per mile) per (origin zone, destination zone, month). Trips are 0.5–20 miles and 1 minute – 3 hours long, with no stats-exclusion flags. |
| Treated | trips with both endpoints inside the CRZ |
| Control (primary) | trips with both endpoints outside Manhattan |
| Control (alternative) | trips within Manhattan that stay outside the CRZ. They may themselves be affected (spillover), so this is a robustness check, not a clean control. |
| Estimand | average post-policy change in treated pace relative to control (ATT), 2025-01 → 2026-09 |
| Estimator | two-way fixed effects (OD pair, month) + treated × calendar-month terms (group-specific seasonality) + treated × post; standard errors clustered by OD pair |
| Checks | (1) naive event study with pre-trend test; (2) **placebo**: the same model with a fake policy date of 2024-01, on 2023–2024 data only, which must be negligible (95% CI within ±0.5%); (3) a group-specific linear trend; (4) the alternative control |
| Validated on synthetic data | `tests/unit/test_policy.py`: the estimator recovers a planted +10% effect, the placebo is null without drift, and the placebo detects planted drift, which the trend term absorbs |

## Results

Positive = slower (more seconds per mile). Values are 95% CIs.

| | Yellow taxi | Uber/Lyft |
|---|---|---|
| OD pairs (treated + control) | 2,314 | 17,779 |
| Naive event study, pre-trend p-value | 0 (fails) | 0 (fails) |
| **Seasonal DiD (main)** | +2.9% (+2.3 to +3.4) | -0.3% (-0.4 to -0.2) |
| Placebo, fake start 2024-01 | +1.4% (+0.9 to +1.8) — **fails** | +0.1% (+0.0 to +0.2) — within ±0.5% |
| With treated-specific linear trend | -0.1% (-0.8 to +0.6) | -3.3% (-3.4 to -3.1) |
| Pre-existing relative drift (trend model) | +1.6%/year | +1.6%/year |
| Alternative control: Manhattan outside CRZ | +2.8% (+2.6 to +3.1) | +0.4% (+0.3 to +0.6) |

**Naive event study:** both sources fail it. Manhattan and outer-borough pace have different seasonal profiles, which is why the main model adds treated × calendar-month terms.

## What this does and doesn't show

- **Not identified for yellow taxis.** The placebo fails: CRZ yellow trips were already slowing relative to outer-borough trips before 2025. The post-policy estimate therefore can't be attributed to the policy.
- **Uber/Lyft:** the placebo is negligible, and the seasonal DiD finds essentially **no change** in CRZ trip pace relative to outer-borough trips. If the pre-period drift is extrapolated instead (trend model), CRZ trips look about 3% faster than their trend would predict. The two designs bracket a range from about −3% to −0.3% against the outer-borough control. Against the rest of Manhattan, which may itself have been affected, CRZ trips were 0.4% slower. Every estimate is small: the data shows no large change in CRZ trip pace in either direction.
- **Coincident changes** this design can't separate from the policy:
  - post-pandemic return-to-office patterns
  - changes in fares and surcharges (including the fee itself, which shifts which trips are taken)
  - roadworks and bus-lane changes
  - the mix of trip purposes
- **Pace is not traffic speed.** It includes pickup and drop-off time, route choice and driver behaviour, and covers only trips that happened. A trip that was deterred by the fee is not in the data.
- **No welfare claim** follows from any of this.
