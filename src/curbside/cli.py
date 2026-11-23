"""Command-line entry point: ``uv run curbside --help``."""

from __future__ import annotations

import json

import typer

from curbside import config, fetch, inspect_source

app = typer.Typer(no_args_is_help=True, help="Curbside AI data pipeline")


@app.command("fetch-trips")
def fetch_trips(source: str, month: str) -> None:
    """Download one monthly TLC trip file and record it in the manifest."""
    src = _source(source)
    row = fetch.fetch_trips(src, month)
    typer.echo(
        f"{src} {month}: "
        + (
            f"downloaded {row.bytes:,} bytes sha256={row.sha256[:12]}…"
            if row
            else "already present, hash verified"
        )
    )


@app.command("fetch-zones")
def fetch_zones() -> None:
    """Download the official taxi-zone shapefile and lookup CSV."""
    rows = fetch.fetch_zones()
    typer.echo(f"zones: {len(rows)} file(s) downloaded" if rows else "zones: already present, verified")


@app.command("latest")
def latest(start: str = "2026-01") -> None:
    """Probe the latest published month per source (HEAD requests only)."""
    for src in config.TRIP_SOURCES:
        typer.echo(f"{src}: {fetch.probe_latest(src, start)}")


@app.command("inspect")
def inspect(source: str, month: str, sample_rows: int = 20) -> None:
    """Write the source-audit report and a reproducible raw sample for one monthly file."""
    src = _source(source)
    path = config.trip_path(src, month)
    lookup = config.RAW / "tlc" / "zones" / "taxi_zone_lookup.csv"
    report = inspect_source.inspect_trip_file(src, month, path, lookup)
    out = config.REPORTS / "audit" / f"{src}_{month}.json"
    inspect_source.write_report(report, out)
    inspect_source.write_sample(
        path, config.ROOT / "docs" / "samples" / f"{src}_{month}_sample.csv", n=sample_rows
    )
    typer.echo(f"wrote {out.relative_to(config.ROOT)} ({report['rows']:,} rows)")


@app.command("inspect-zones")
def inspect_zones() -> None:
    """Write the zone-geometry audit report."""
    zdir = config.RAW / "tlc" / "zones"
    report = inspect_source.inspect_zones(zdir / "taxi_zones.zip", zdir / "taxi_zone_lookup.csv")
    out = config.REPORTS / "audit" / "zones.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n")
    typer.echo(f"wrote {out.relative_to(config.ROOT)}")


def _months(start: str | None, end: str | None) -> list[str]:
    return config.month_range(start or config.WINDOW_START, end or config.WINDOW_END)


def _sources(source: str | None) -> list[config.TripSource]:
    return [_source(source)] if source else list(config.TRIP_SOURCES)


@app.command("fetch-range")
def fetch_range(source: str | None = None, start: str | None = None, end: str | None = None) -> None:
    """Download every month in the window (skips files whose hash already matches)."""
    from curbside import pipeline

    for src in _sources(source):
        for month in _months(start, end):
            sha = pipeline._manifest_sha(config.trip_url(src, month))
            if sha and not config.trip_path(src, month).exists():
                from curbside import silver

                if silver.silver_is_current(src, month, sha):
                    continue  # raw pruned by policy D11; silver is the retained record
            row = fetch.fetch_trips(src, month)
            if row:
                typer.echo(f"{src} {month}: {row.bytes:,} bytes")


@app.command("build-zones")
def build_zones() -> None:
    """Zone table (area, label points) and simplified web GeoJSON."""
    from curbside import pipeline

    typer.echo(json.dumps(pipeline.build_zones()))


@app.command("build")
def build(source: str | None = None, start: str | None = None, end: str | None = None) -> None:
    """raw → silver → gold (zone-hour + daily OD) for the window, with invariant checks."""
    from curbside import pipeline

    months, sources = _months(start, end), _sources(source)
    pipeline.build_silver_all(sources, months, log=typer.echo)
    pipeline.build_gold_all(sources, months, log=typer.echo)


@app.command("reconcile")
def reconcile_cmd(start: str | None = None, end: str | None = None) -> None:
    """Compare monthly zone totals with TLC's aggregate (NYC Open Data c5iv-bn4s)."""
    from curbside import pipeline

    out = pipeline.run_reconciliation(list(config.TRIP_SOURCES), _months(start, end))
    for src, r in out.items():
        for name, v in r["variants"].items():
            typer.echo(
                f"{src:6} {name:28} max|rel diff|={v['max_abs_rel_diff']:.5f} "
                f"zone exact={v['mean_zone_exact_match_share']:.3f} ok={v['all_months_within_tolerance']}"
            )
        typer.echo(f"{src}: best = {r['best_variant']}")


@app.command("qa")
def qa(start: str | None = None, end: str | None = None) -> None:
    """Write reports/qa/qa_summary.json from silver stats, gold checks and reconciliation."""
    from curbside import pipeline

    s = pipeline.write_qa_summary(list(config.TRIP_SOURCES), _months(start, end))
    for src, v in s["sources"].items():
        typer.echo(
            f"{src}: {v['months']} months, {v['raw_rows_total']:,} raw rows, "
            f"{v['gold_trip_total']:,} gold trips, invariants={v['gold_invariants_pass']}, "
            f"recon={v['reconciliation_best_variant']} ok={v['reconciliation_within_tolerance']}"
        )


@app.command("prune-raw")
def prune_raw(start: str | None = None, end: str | None = None) -> None:
    """Delete HVFHV raw files that passed every gate (policy D11). Yellow raw is always kept."""
    from curbside import pipeline

    pruned = pipeline.prune_raw("hvfhv", _months(start, end), log=typer.echo)
    typer.echo(f"pruned {len(pruned)} HVFHV raw file(s)")


@app.command("basemap")
def basemap_cmd(maxzoom: int = 14) -> None:
    """Cut the self-hosted NYC basemap from the Protomaps daily build, plus fonts and sprites."""
    from curbside import basemap

    typer.echo(json.dumps(basemap.extract(maxzoom=maxzoom, log=typer.echo)))
    typer.echo(json.dumps(basemap.fetch_assets()))


@app.command("build-web")
def build_web(start: str | None = None, end: str | None = None) -> None:
    """Write the static web artifacts (web/public/data) from gold and the QA report."""
    from curbside import artifacts

    res = artifacts.build(months=_months(start, end))
    typer.echo(f"web artifacts: {res['months']} months, {res['bytes'] / 1e6:.1f} MB")
    if (config.DATA / "forecasts" / "task_a").exists() or (config.ROOT / "forecasts" / "task_b").exists():
        from curbside import artifacts_forecast

        fres = artifacts_forecast.build()
        typer.echo(f"forecast artifacts: {fres['bytes'] / 1e6:.1f} MB")
    from curbside import artifacts_flows

    flres = artifacts_flows.build(months=_months(start, end))
    typer.echo(f"flow artifacts: {flres['bytes'] / 1e6:.1f} MB")
    study = config.REPORTS / "policy" / "policy_study.json"
    if study.exists():
        d = json.loads(study.read_text())
        for s in d["sources"].values():  # the web page needs summaries, not the per-month coefficients
            s["event_study_naive"] = {k: v for k, v in s["event_study_naive"].items() if k != "coefficients"}
        d["crz"] = {k: v for k, v in d["crz"].items() if k != "evidence"}
        (artifacts.WEB_DATA / "policy.json").write_text(json.dumps(d, separators=(",", ":")))
        typer.echo("policy study: written")


@app.command("forecast")
def forecast_cmd(
    source: str | None = None,
    skip_task_a: bool = False,
    freeze: bool = True,
) -> None:
    """Task A rolling backtests, Task B backtest + freeze of the next unpublished month."""
    from curbside import forecast, weather

    months = _months(None, None)
    out = config.REPORTS / "forecast"
    out.mkdir(parents=True, exist_ok=True)
    sources = _sources(source)
    if not skip_task_a:
        w, cov = weather.load(months)
        (config.QA / "weather_coverage.json").write_text(json.dumps(cov, indent=1) + "\n")
        for src in sources:
            res = forecast.run_task_a(src, months, weather=w, log=typer.echo)
            (out / f"task_a_{src}.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    merged = {}
    for src in config.TRIP_SOURCES:
        f = out / f"task_a_{src}.json"
        if f.exists():
            merged[src] = json.loads(f.read_text())
    (out / "task_a_metrics.json").write_text(json.dumps(merged, indent=1) + "\n")

    test_months = months[-12:]  # Task B backtest: the last 12 published months
    backtests = {src: forecast.run_task_b_backtest(src, months, test_months) for src in config.TRIP_SOURCES}
    (out / "task_b_backtest.json").write_text(json.dumps(backtests, indent=1, default=str) + "\n")
    for src, bt in backtests.items():
        typer.echo(f"task B {src}: mean WAPE {bt['mean_wape']} -> {bt['best_method']}")

    target = config.next_month(config.WINDOW_END)
    if freeze:
        for src, bt in backtests.items():
            path = config.ROOT / "forecasts" / "task_b" / target / f"{src}.manifest.json"
            if path.exists():
                typer.echo(f"task B {src} {target}: already frozen, not overwritten")
                continue
            man = forecast.freeze_task_b(src, months, target, bt["conformal_q"], method=bt["best_method"])
            typer.echo(f"task B {src} {target}: frozen sha256={man['sha256'][:12]}")

    scored = {}
    for d in sorted((config.ROOT / "forecasts" / "task_b").glob("*")):
        for src in config.TRIP_SOURCES:
            r = forecast.score_frozen_task_b(src, d.name)
            if r:
                scored.setdefault(d.name, {})[src] = r
    (out / "task_b_scored.json").write_text(json.dumps(scored, indent=1) + "\n")


@app.command("graph-ablation")
def graph_ablation(source: str | None = None) -> None:
    """Retrain Task A with adjacency (graph) features and compare with the plain GBM."""
    from curbside import forecast, graph

    ids, W = graph.adjacency()
    months = _months(None, None)
    out = config.REPORTS / "graph_ablation"
    out.mkdir(parents=True, exist_ok=True)
    for src in _sources(source):
        p = forecast.load_panel(src, months[:1])
        if p.zone_ids != ids:
            raise RuntimeError("adjacency zone order differs from the gold panel")
        res = forecast.run_task_a(src, months, graph_W=W, only=["gbm_graph"], write=False, log=typer.echo)
        (out / f"graph_ablation_{src}.json").write_text(json.dumps(res, indent=1, default=str) + "\n")


@app.command("policy")
def policy_cmd() -> None:
    """Congestion-pricing study on TLC trip pace (descriptive + seasonal DiD with placebo)."""
    from curbside import policy

    res = policy.run_study(_months(None, None), log=typer.echo)
    out = config.REPORTS / "policy"
    out.mkdir(parents=True, exist_ok=True)
    (out / "policy_study.json").write_text(json.dumps(res, indent=1, default=str) + "\n")


@app.command("refresh")
def refresh(apply: bool = typer.Option(False, help="Run the steps; default is a dry run")) -> None:
    """Monthly update: ingest newly published months, reconcile, QA, score frozen Task B forecasts,
    freeze the next one, rebuild web artifacts. Task A backtests are not re-run (use `forecast`)."""
    latest = {
        src: fetch.probe_latest(src, config.next_month(config.WINDOW_END)) for src in config.TRIP_SOURCES
    }
    new_end = min((m for m in latest.values() if m), default=None)
    if not new_end:
        nxt = config.next_month(config.WINDOW_END)
        typer.echo(f"nothing new: window ends {config.WINDOW_END}; TLC has not published {nxt}")
        return
    months = config.month_range(config.next_month(config.WINDOW_END), new_end)
    typer.echo(f"new months for both sources: {months}")
    steps = [
        "fetch-range",
        "build (silver + gold)",
        "reconcile",
        "qa",
        "prune-raw (HVFHV, gated)",
        "forecast --skip-task-a (score frozen Task B, freeze next month)",
        "build-web",
    ]
    if not apply:
        typer.echo("dry run; would run: " + " -> ".join(steps))
        return
    from curbside import pipeline

    window = config.ROOT / "data" / "window.json"
    window.write_text(json.dumps({"start": config.WINDOW_START, "end": new_end}) + "\n")
    config.WINDOW_END = new_end
    for src in config.TRIP_SOURCES:
        for m in months:
            fetch.fetch_trips(src, m)
    all_months = _months(None, None)
    pipeline.build_silver_all(list(config.TRIP_SOURCES), all_months, log=typer.echo)
    pipeline.build_gold_all(list(config.TRIP_SOURCES), all_months, log=typer.echo)
    pipeline.run_reconciliation(list(config.TRIP_SOURCES), all_months)
    pipeline.write_qa_summary(list(config.TRIP_SOURCES), all_months)
    pipeline.prune_raw("hvfhv", all_months, log=typer.echo)
    forecast_cmd(source=None, skip_task_a=True, freeze=True)
    build_web(start=None, end=None)
    typer.echo(f"refreshed through {new_end}; review and commit as a data refresh")


def _source(source: str) -> config.TripSource:
    if source not in config.TRIP_SOURCES:
        raise typer.BadParameter(f"source must be one of {config.TRIP_SOURCES}")
    return source  # type: ignore[return-value]


def main() -> None:
    app()
