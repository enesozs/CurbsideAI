"""Congestion-pricing study (descriptive + difference-in-differences), TLC part.

Estimand (primary): the change in travel pace (seconds per mile) of trips with both endpoints
inside the Congestion Relief Zone (CRZ), relative to comparable trips that never touch Manhattan,
after 2025-01-05 — an average effect on treated trips (ATT) under parallel trends.

- CRZ membership of taxi zones is derived from TLC's own ``cbd_congestion_fee`` field: a zone is
  inside if, in 2025+, at least 60% of its intra-zone trips were charged the fee. No hand-drawn
  boundary, and the rule is reported with its evidence.
- Pace = trip duration / distance, from silver trips without stats-exclusion flags, distance in
  [0.5, 20] miles, duration in [60 s, 3 h]. Aggregated to (OD pair, month) medians.
- Model: log(median pace) ~ pair FE + month FE + Σ_k β_k·treated·1[event month k], reference
  month 2024-12, SEs clustered by OD pair. Pre-period β_k test parallel trends.

Pace is a speed proxy for *taxi/for-hire trips*, not a measurement of all traffic.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import polars as pl

from curbside import config

POLICY_START = date(2025, 1, 5)
REFERENCE_MONTH = "2024-12"
# Placebo equivalence margin: a placebo "effect" whose whole 95% CI lies within ±0.5% is treated as
# negligible (with ~10^4 OD pairs, practically zero effects are statistically significant).
PLACEBO_MARGIN_PCT = 0.5
# Fee incidence on intra-zone trips is bimodal: CRZ zones 0.72–1.0 (edge zones along the toll-exempt
# FDR Drive / West Side Highway sit lowest), zones straddling or outside 60th St ≤ 0.45. 0.6 sits in the gap.
CRZ_SHARE = 0.6


def classify_crz_zones(months: list[str]) -> dict[str, Any]:
    """Zones inside the CRZ by fee incidence on intra-zone trips (both sources, 2025+ files)."""
    post = [m for m in months if m >= "2025-01"]
    paths = [
        config.silver_path(s, m).as_posix()
        for s in config.TRIP_SOURCES
        for m in post
        if config.silver_path(s, m).exists()
    ]
    con = config.db()
    rows = con.sql(
        f"""SELECT pickup_zone_id AS zone, count(*) AS n,
                   avg((cbd_congestion_fee_usd > 0)::INT) AS fee_share
            FROM read_parquet({paths})
            WHERE pickup_zone_id = dropoff_zone_id AND cbd_congestion_fee_usd IS NOT NULL
              AND pickup_ts_raw >= TIMESTAMP '2025-01-06'
            GROUP BY 1 ORDER BY 1"""
    ).pl()
    con.close()
    inside = rows.filter((pl.col("fee_share") >= CRZ_SHARE) & (pl.col("n") >= 100))
    return {
        "rule": f"intra-zone trips charged cbd_congestion_fee >= {CRZ_SHARE:.0%} (n >= 100), 2025-01-06+",
        "zones": sorted(int(z) for z in inside["zone"]),
        "evidence": {
            int(z): {"n": int(n), "fee_share": round(float(f), 4)}
            for z, n, f in rows.select("zone", "n", "fee_share").iter_rows()
        },
    }


def pace_panel(
    source: config.TripSource, months: list[str], crz: list[int], borough: dict[int, str]
) -> pl.DataFrame:
    """(origin, destination, month) median pace for treated / control / adjacent groups.

    Computed one pickup month at a time (exact medians, bounded memory): month M reads silver
    files M-1..M+1 filtered to pickups in M, like gold.
    """
    crz_sql = "(" + ",".join(map(str, crz)) + ")"
    manhattan = [z for z, b in borough.items() if b == "Manhattan"]
    man_sql = "(" + ",".join(map(str, manhattan)) + ")"
    frames = []
    con = config.db()
    for m in months:
        paths = [
            config.silver_path(source, x).as_posix()
            for x in (config.prev_month(m), m, config.next_month(m))
            if config.silver_path(source, x).exists()
        ]
        if not paths:
            continue
        frames.append(
            con.sql(
                f"""WITH t AS (
                  SELECT pickup_zone_id AS o, dropoff_zone_id AS d,
                         trip_duration_s / trip_distance_miles AS pace
                  FROM read_parquet({paths})
                  WHERE flags & {config.STATS_EXCLUDE_MASK} = 0
                    AND strftime(pickup_local_hour, '%Y-%m') = '{m}'
                    AND trip_distance_miles BETWEEN 0.5 AND 20 AND trip_duration_s BETWEEN 60 AND 10800
                )
                SELECT o, d, '{m}' AS month,
                       CASE WHEN o IN {crz_sql} AND d IN {crz_sql} THEN 'treated'
                            WHEN o NOT IN {man_sql} AND d NOT IN {man_sql} AND o NOT IN (264, 265)
                                 AND d NOT IN (264, 265) THEN 'control'
                            WHEN o IN {man_sql} AND d IN {man_sql} THEN 'adjacent'
                            ELSE 'other' END AS grp,
                       median(pace) AS pace, count(*) AS n
                FROM t GROUP BY 1, 2, 3, 4"""
            ).pl()
        )
    con.close()
    return pl.concat(frames).filter(pl.col("grp") != "other")


def event_study(
    panel: pl.DataFrame,
    treated: str = "treated",
    control: str = "control",
    min_n: int = 20,
    min_months: int = 24,
) -> dict[str, Any]:
    """Two-way FE event study with pair-clustered SEs (numpy implementation, transparent)."""
    df = panel.filter(pl.col("grp").is_in([treated, control]) & (pl.col("n") >= min_n))
    # balanced-ish: pairs observed in enough months, both before and after
    keep = (
        df.group_by("o", "d")
        .agg(
            pl.len().alias("m"),
            (pl.col("month") < "2025-01").any().alias("pre"),
            (pl.col("month") >= "2025-01").any().alias("post"),
        )
        .filter((pl.col("m") >= min_months) & pl.col("pre") & pl.col("post"))
    )
    df = df.join(keep.select("o", "d"), on=["o", "d"]).with_columns(
        pl.col("pace").log().alias("y"), (pl.col("grp") == treated).cast(pl.Int8).alias("tr")
    )
    months = sorted(df["month"].unique().to_list())
    event_months = [m for m in months if m != REFERENCE_MONTH]
    pair_id = df.select(pl.col("o").cast(pl.Utf8) + "-" + pl.col("d").cast(pl.Utf8)).to_series()
    pairs = {p: i for i, p in enumerate(pair_id.unique().to_list())}
    g = np.array([pairs[p] for p in pair_id])
    mi = {m: i for i, m in enumerate(months)}
    t = np.array([mi[m] for m in df["month"]])
    y = df["y"].to_numpy()
    tr = df["tr"].to_numpy()
    # interaction columns tr × 1[month = k] for k != reference
    X_int = np.zeros((len(y), len(event_months)))
    for j, m in enumerate(event_months):
        X_int[:, j] = tr * (t == mi[m])

    # absorb pair and month FE by alternating projections (within transformation)
    def demean(v: np.ndarray) -> np.ndarray:
        v = v.copy()
        for _ in range(50):
            v -= np.bincount(g, v, minlength=len(pairs))[g] / np.bincount(g, minlength=len(pairs))[g]
            v -= np.bincount(t, v, minlength=len(months))[t] / np.bincount(t, minlength=len(months))[t]
        return v

    yd = demean(y)
    Xd = np.column_stack([demean(X_int[:, j]) for j in range(X_int.shape[1])])
    XtX = Xd.T @ Xd
    beta = np.linalg.solve(XtX, Xd.T @ yd)
    resid = yd - Xd @ beta
    # cluster-robust (CR1) by pair
    meat = np.zeros_like(XtX)
    for c in range(len(pairs)):
        idx = g == c
        s = Xd[idx].T @ resid[idx]
        meat += np.outer(s, s)
    G, N, K = len(pairs), len(y), Xd.shape[1]
    bread = np.linalg.inv(XtX)
    V = bread @ meat @ bread * (G / (G - 1)) * ((N - 1) / (N - K - len(pairs) - len(months) + 1))
    se = np.sqrt(np.diag(V))
    coefs = [
        {
            "month": m,
            "beta": float(b),
            "se": float(s),
            "pct": float(np.expm1(b) * 100),
            "ci_low_pct": float(np.expm1(b - 1.96 * s) * 100),
            "ci_high_pct": float(np.expm1(b + 1.96 * s) * 100),
        }
        for m, b, s in zip(event_months, beta, se, strict=True)
    ]
    pre = [j for j, m in enumerate(event_months) if m < REFERENCE_MONTH]
    post = [j for j, m in enumerate(event_months) if m >= "2025-01"]
    # Wald test that all pre-period coefficients are zero
    bp = beta[pre]
    Vp = V[np.ix_(pre, pre)]
    wald = float(bp @ np.linalg.solve(Vp, bp))
    from scipy import stats

    p_pre = float(1 - stats.chi2.cdf(wald, len(pre)))
    # average post effect: mean of post coefficients with its SE
    w = np.zeros(K)
    w[post] = 1 / len(post)
    avg = float(w @ beta)
    avg_se = float(np.sqrt(w @ V @ w))
    return {
        "treated": treated,
        "control": control,
        "pairs": {
            "treated": int(df.filter(pl.col("tr") == 1).select("o", "d").unique().height),
            "control": int(df.filter(pl.col("tr") == 0).select("o", "d").unique().height),
        },
        "observations": int(N),
        "reference_month": REFERENCE_MONTH,
        "coefficients": coefs,
        "pretrend_wald_chi2": wald,
        "pretrend_df": len(pre),
        "pretrend_p": p_pre,
        "avg_post_effect_pct": float(np.expm1(avg) * 100),
        "avg_post_ci_pct": [
            float(np.expm1(avg - 1.96 * avg_se) * 100),
            float(np.expm1(avg + 1.96 * avg_se) * 100),
        ],
    }


def _prep(
    panel: pl.DataFrame, treated: str, control: str, min_n: int, min_months: int, split: str
) -> pl.DataFrame:
    df = panel.filter(pl.col("grp").is_in([treated, control]) & (pl.col("n") >= min_n))
    keep = (
        df.group_by("o", "d")
        .agg(
            pl.len().alias("m"),
            (pl.col("month") < split).any().alias("pre"),
            (pl.col("month") >= split).any().alias("post"),
        )
        .filter((pl.col("m") >= min_months) & pl.col("pre") & pl.col("post"))
    )
    return df.join(keep.select("o", "d"), on=["o", "d"]).with_columns(
        pl.col("pace").log().alias("y"), (pl.col("grp") == treated).cast(pl.Int8).alias("tr")
    )


def _twfe(df: pl.DataFrame, X: np.ndarray) -> tuple[np.ndarray, np.ndarray, int, int]:
    """OLS of y on X with pair and month fixed effects absorbed; CR1 covariance clustered by pair."""
    pair_id = (df["o"].cast(pl.Utf8) + "-" + df["d"].cast(pl.Utf8)).to_numpy()
    _, g = np.unique(pair_id, return_inverse=True)
    _, t = np.unique(df["month"].to_numpy(), return_inverse=True)
    G, M = g.max() + 1, t.max() + 1
    cg, ct = np.bincount(g, minlength=G), np.bincount(t, minlength=M)

    def demean(v: np.ndarray) -> np.ndarray:
        v = v.astype(np.float64).copy()
        for _ in range(100):
            v -= (np.bincount(g, v, minlength=G) / cg)[g]
            v -= (np.bincount(t, v, minlength=M) / ct)[t]
        return v

    yd = demean(df["y"].to_numpy())
    Xd = np.column_stack([demean(X[:, j]) for j in range(X.shape[1])])
    XtX = Xd.T @ Xd
    beta = np.linalg.solve(XtX, Xd.T @ yd)
    resid = yd - Xd @ beta
    S = np.zeros((G, X.shape[1]))
    np.add.at(S, g, Xd * resid[:, None])
    meat = S.T @ S
    bread = np.linalg.inv(XtX)
    N, K = len(yd), X.shape[1]
    V = bread @ meat @ bread * (G / (G - 1)) * ((N - 1) / max(1, N - K - G - M + 1))
    return beta, V, int(G), int(N)


def did_seasonal(
    panel: pl.DataFrame,
    post_start: str = "2025-01",
    end: str | None = None,
    treated: str = "treated",
    control: str = "control",
    min_n: int = 20,
    min_months: int = 24,
) -> dict[str, Any]:
    """ATT on log pace with pair FE, month FE and treated × calendar-month (group-specific seasonality).

    Pre-period months identify each group's seasonal profile; the treated × post coefficient is the
    average post-period deviation from it, relative to the control group.
    """
    p = panel if end is None else panel.filter(pl.col("month") < end)
    df = _prep(p, treated, control, min_n, min_months, post_start)
    tr = df["tr"].to_numpy().astype(float)
    moy = df["month"].str.slice(5, 2).cast(pl.Int32).to_numpy()
    post = (df["month"] >= post_start).to_numpy().astype(float)
    cols = [tr * post] + [tr * (moy == m) for m in range(2, 13)]
    beta, V, G, N = _twfe(df, np.column_stack(cols))
    b, se = float(beta[0]), float(np.sqrt(V[0, 0]))
    return {
        "post_start": post_start,
        "end": end,
        "treated": treated,
        "control": control,
        "pairs": G,
        "observations": N,
        "att_pct": float(np.expm1(b) * 100),
        "ci_pct": [float(np.expm1(b - 1.96 * se) * 100), float(np.expm1(b + 1.96 * se) * 100)],
        "z": b / se,
    }


def yearly_profile(
    panel: pl.DataFrame, treated: str = "treated", control: str = "control", min_n: int = 20
) -> list[dict[str, Any]]:
    """Descriptive: median pace by group and month (trip-pair medians averaged), for charts."""
    df = panel.filter(pl.col("grp").is_in([treated, control, "adjacent"]) & (pl.col("n") >= min_n))
    out = (
        df.group_by("grp", "month")
        .agg((pl.col("pace") * pl.col("n")).sum().alias("w"), pl.col("n").sum().alias("n"))
        .with_columns((pl.col("w") / pl.col("n") / 60 * 1).alias("min_per_mile"))
        .sort("grp", "month")
    )
    return [
        {"group": g, "month": m, "min_per_mile": round(float(v), 3), "trips": int(n)}
        for g, m, v, n in out.select("grp", "month", "min_per_mile", "n").iter_rows()
    ]


def did_with_trend(
    panel: pl.DataFrame,
    post_start: str = "2025-01",
    treated: str = "treated",
    control: str = "control",
    min_n: int = 20,
    min_months: int = 24,
) -> dict[str, Any]:
    """Sensitivity: seasonal DiD plus a treated-specific linear time trend."""
    df = _prep(panel, treated, control, min_n, min_months, post_start)
    tr = df["tr"].to_numpy().astype(float)
    moy = df["month"].str.slice(5, 2).cast(pl.Int32).to_numpy()
    yr = df["month"].str.slice(0, 4).cast(pl.Int32).to_numpy()
    t = (yr - 2023) * 12 + moy
    post = (df["month"] >= post_start).to_numpy().astype(float)
    cols = [tr * post, tr * (t - t.mean())] + [tr * (moy == m) for m in range(2, 13)]
    beta, V, G, N = _twfe(df, np.column_stack(cols))
    b, se = float(beta[0]), float(np.sqrt(V[0, 0]))
    return {
        "att_pct": float(np.expm1(b) * 100),
        "ci_pct": [float(np.expm1(b - 1.96 * se) * 100), float(np.expm1(b + 1.96 * se) * 100)],
        "trend_pct_per_year": float(np.expm1(beta[1] * 12) * 100),
        "pairs": G,
        "observations": N,
    }


def entry_flows(source: config.TripSource, months: list[str], crz: list[int]) -> list[dict[str, Any]]:
    """Descriptive: monthly trips by relation to the CRZ (inside, entering, leaving, outside)."""
    paths = [
        config.gold_od_path(source, m).as_posix() for m in months if config.gold_od_path(source, m).exists()
    ]
    crz_sql = "(" + ",".join(map(str, crz)) + ")"
    con = config.db()
    rows = con.sql(
        f"""SELECT strftime(local_date, '%Y-%m') AS month,
                   CASE WHEN origin_zone_id IN {crz_sql} AND destination_zone_id IN {crz_sql} THEN 'inside'
                        WHEN destination_zone_id IN {crz_sql} THEN 'entering'
                        WHEN origin_zone_id IN {crz_sql} THEN 'leaving'
                        ELSE 'outside' END AS relation,
                   sum(trip_count) AS trips, count(DISTINCT local_date) AS days
            FROM read_parquet({paths}) GROUP BY 1, 2 ORDER BY 1, 2"""
    ).pl()
    con.close()
    return [
        {"month": m, "relation": r, "trips_per_day": round(float(t) / d, 1)}
        for m, r, t, d in rows.select("month", "relation", "trips", "days").iter_rows()
    ]


def run_study(months: list[str], log=print) -> dict[str, Any]:
    """Full study (TLC-only): zone classification, descriptives, seasonal DiD with robustness checks."""
    import pandas as pd

    from curbside import zones

    crz = classify_crz_zones([m for m in months if m >= "2025-01"])
    zt = pd.read_parquet(zones.zones_dir() / "zones.parquet")
    borough = dict(zip(zt.LocationID.astype(int), zt.borough, strict=True))
    out: dict[str, Any] = {"policy_start": str(POLICY_START), "crz": crz, "sources": {}}
    for source in config.TRIP_SOURCES:
        pan = pace_panel(source, months, crz["zones"], borough)
        res = {
            "pace_by_month": yearly_profile(pan),
            "entry_flows": entry_flows(source, months, crz["zones"]),
            "event_study_naive": event_study(pan),
            "did_seasonal": did_seasonal(pan),
            "did_seasonal_adjacent_control": did_seasonal(pan, control="adjacent"),
            "placebo_2024": did_seasonal(pan, post_start="2024-01", end="2025-01", min_months=12),
            "did_trend": did_with_trend(pan),
        }
        a, pl_ = res["did_seasonal"], res["placebo_2024"]
        placebo_ok = -PLACEBO_MARGIN_PCT <= pl_["ci_pct"][0] and pl_["ci_pct"][1] <= PLACEBO_MARGIN_PCT
        pre_ok = res["event_study_naive"]["pretrend_p"] > 0.05
        res["verdict"] = {
            "placebo_passes": placebo_ok,
            "naive_pretrend_passes": pre_ok,
            "credible_causal_estimate": placebo_ok,
            "summary": (
                f"Seasonal DiD: {a['att_pct']:+.1f}% pace (CI {a['ci_pct'][0]:+.1f}..{a['ci_pct'][1]:+.1f}); "
                f"2024 placebo {pl_['att_pct']:+.1f}% "
                f"(CI {pl_['ci_pct'][0]:+.1f}..{pl_['ci_pct'][1]:+.1f}) — "
                + (
                    f"placebo within ±{PLACEBO_MARGIN_PCT}% (negligible)."
                    if placebo_ok
                    else "placebo FAILS: pre-existing differential drift; no causal claim."
                )
            ),
        }
        log(f"{source}: {res['verdict']['summary']}")
        out["sources"][source] = res
    return out
