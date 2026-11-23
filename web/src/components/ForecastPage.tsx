import { useEffect, useMemo, useRef, useState } from "react";
import * as Plot from "@observablehq/plot";
import { a11yPlot } from "../lib/plot";
import { loadMonth, type Meta, type MonthData, type Source } from "../lib/data";
import { dayLabel, groupByDay, hourInfos, monthLabel } from "../lib/time";

const NA = 65535;
const NAMES: Record<Source, string> = { yellow: "Yellow taxi", hvfhv: "Uber/Lyft" };
const MODEL_LABELS: Record<string, string> = {
  gbm: "Gradient boosting",
  gbm_weather: "Gradient boosting + weather",
  glm: "Poisson GLM",
  b_mean_4w: "Baseline: 4-week same-hour mean",
  b_snaive_1w: "Baseline: same hour last week",
  b_last_same_hour: "Baseline: last same hour known",
};
const WAPE_SEGMENTS = ["all", "high", "mid", "low"] as const; // sparse zones: WAPE is unstable, show MAE

type SegMetrics = { n: number; mae: number | null; wape: number | null; peak_mae?: number | null; coverage?: number; mean_width?: number };
interface ForecastIndex {
  task_a: Record<Source, { months: string[] }>;
  task_b: Record<Source, { live: { target_month: string; issued_at_utc: string; information_cutoff_utc: string; sha256: string; model_id: string; start_utc: number; hours: number }[] }>;
  task_a_metrics?: Record<Source, { folds: Record<string, { test: [string, string]; models: Record<string, Record<string, SegMetrics>> }> }>;
  task_b_backtest?: Record<Source, { months: Record<string, Record<string, Record<string, SegMetrics>>> }>;
  task_b_scored?: Record<string, Record<Source, Record<string, SegMetrics>>>;
}

function cssVar(n: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim();
}

async function loadForecast(source: Source, month: string): Promise<Uint16Array> {
  const r = await fetch(`/data/forecast/a/${source}/${month}.bin`);
  if (!r.ok) throw new Error(`forecast ${month}: HTTP ${r.status}`);
  return new Uint16Array(await r.arrayBuffer());
}

function pct(v: number | null | undefined, digits = 1) {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(digits)}%`;
}

export function ForecastPage({ meta, theme }: { meta: Meta; theme: string }) {
  const [idx, setIdx] = useState<ForecastIndex | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [source, setSource] = useState<Source>("hvfhv");
  const [month, setMonth] = useState("");
  const [zone, setZone] = useState<number>(-1); // -1 = citywide
  const [week, setWeek] = useState(0);
  const [md, setMd] = useState<MonthData | null>(null);
  const [fc, setFc] = useState<Uint16Array | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/data/forecast/index.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: ForecastIndex) => {
        setIdx(d);
        const ms = d.task_a.hvfhv?.months ?? [];
        setMonth(ms[ms.length - 1] ?? "");
      })
      .catch((e) => setErr(String(e.message || e)));
  }, []);

  useEffect(() => {
    if (!idx || !month) return;
    let alive = true;
    Promise.all([loadMonth(meta, month), loadForecast(source, month)])
      .then(([m, f]) => {
        if (alive) {
          setMd(m);
          setFc(f);
        }
      })
      .catch((e) => alive && setErr(String(e.message || e)));
    return () => {
      alive = false;
    };
  }, [idx, month, source, meta]);

  const infos = useMemo(() => (md ? hourInfos(md.startUtc, md.hours) : []), [md]);
  const days = useMemo(() => [...groupByDay(infos).keys()], [infos]);
  const weeks = Math.max(1, Math.ceil(days.length / 7));
  const weekDays = days.slice(week * 7, week * 7 + 7);

  const series = useMemo(() => {
    if (!md || !fc || !md.counts[source]) return null;
    const nZ = meta.zones.length;
    const zi = zone === -1 ? -1 : meta.zones.findIndex((z) => z.id === zone);
    const y = md.counts[source]!;
    const rows: { t: Date; actual: number; point: number | null; lo: number | null; hi: number | null }[] = [];
    for (const h of infos) {
      if (!weekDays.includes(h.day)) continue;
      let a = 0;
      let p = 0;
      let missing = false;
      const zs = zi === -1 ? [...Array(nZ).keys()] : [zi];
      for (const k of zs) {
        a += y[h.index * nZ + k];
        const v = fc[(h.index * nZ + k) * 3];
        if (v === NA) missing = true;
        else p += v;
      }
      const lo = zi >= 0 ? fc[(h.index * nZ + zi) * 3 + 1] : NA;
      const hi = zi >= 0 ? fc[(h.index * nZ + zi) * 3 + 2] : NA;
      rows.push({
        t: new Date(h.utc * 1000),
        actual: a,
        point: missing ? null : p,
        lo: lo === NA ? null : lo,
        hi: hi === NA ? null : hi,
      });
    }
    return rows;
  }, [md, fc, source, zone, infos, weekDays, meta]);

  useEffect(() => {
    const el = chartRef.current;
    if (!el || !series) return;
    const color = cssVar(`--series-${source}`);
    const ink = cssVar("--ink");
    const plot = Plot.plot({
      width: el.clientWidth || 800,
      height: 300,
      marginLeft: 48,
      style: { background: "transparent", color: cssVar("--muted"), fontSize: "11px", fontFamily: "inherit" },
      x: { type: "time", tickFormat: "%a %-d", ticks: "day", label: null },
      y: { grid: true, label: "pickups / hour", tickFormat: "~s", zero: true },
      marks: [
        Plot.gridY({ stroke: cssVar("--hairline"), strokeOpacity: 1 }),
        Plot.areaY(series.filter((d) => d.lo !== null), { x: "t", y1: "lo", y2: "hi", fill: color, fillOpacity: 0.18, curve: "step" }),
        Plot.line(series, { x: "t", y: "actual", stroke: ink, strokeWidth: 1.6 }),
        Plot.line(series.filter((d) => d.point !== null), { x: "t", y: "point", stroke: color, strokeWidth: 2, strokeDasharray: "5,3" }),
        Plot.ruleX(series, Plot.pointerX({ x: "t", stroke: cssVar("--muted"), strokeOpacity: 0.6 })),
        Plot.tip(
          series,
          Plot.pointerX({
            x: "t",
            y: "actual",
            title: (d: (typeof series)[number]) =>
              `${d.t.toLocaleString("en-US", { timeZone: "America/New_York", weekday: "short", month: "short", day: "numeric", hour: "numeric" })}\nObserved: ${d.actual.toLocaleString("en-US")}\nForecast: ${d.point === null ? "—" : d.point.toLocaleString("en-US")}${d.lo !== null ? `\n90% interval: ${d.lo}–${d.hi}` : ""}`,
            fill: cssVar("--surface"),
          }),
        ),
      ],
    });
    a11yPlot(plot, "Observed hourly pickups compared with day-ahead forecasts");
    el.replaceChildren(plot);
    return () => plot.remove();
  }, [series, source, theme]);

  if (err) return <div className="p-8 text-[var(--ink-2)]">Forecast artifacts are not available ({err}).</div>;
  if (!idx) return <div className="p-8 text-[var(--ink-2)]">Loading forecasts…</div>;

  const metrics = idx.task_a_metrics?.[source];
  const folds = metrics ? Object.keys(metrics.folds) : [];
  const models = metrics && folds.length ? Object.keys(metrics.folds[folds[0]].models) : [];
  const avg = (model: string, seg: string, key: keyof SegMetrics) => {
    const vals = folds.map((f) => metrics!.folds[f].models[model]?.[seg]?.[key]).filter((v): v is number => typeof v === "number");
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  };
  const baselineBest = Math.min(...["b_mean_4w", "b_snaive_1w", "b_last_same_hour"].map((m) => avg(m, "all", "wape") ?? Infinity));
  const live = idx.task_b[source]?.live ?? [];
  const bt = idx.task_b_backtest?.[source];

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-8 space-y-6">
      <header>
        <h1 className="font-display text-[32px] leading-tight">Forecast Studio</h1>
        <p className="mt-2 text-[var(--ink-2)] max-w-3xl">
          <strong className="text-[var(--ink)]">Day-ahead backtest:</strong> each forecast is issued at 6 PM New York time for every hour of the next day, using only pickups that happened before 6 PM.
          That is what an operator with its <em>own</em> live trip feed could do — the public TLC data arrives about two months late, so these are out-of-sample tests on history, not live predictions.
        </p>
      </header>

      <section className="panel p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="seg" role="group" aria-label="Service">
            {(["yellow", "hvfhv"] as Source[]).map((s) => (
              <button key={s} type="button" aria-pressed={source === s} onClick={() => setSource(s)}>
                <span className="inline-block h-2 w-2 rounded-full mr-1.5 align-middle" style={{ background: `var(--series-${s})` }} />
                {NAMES[s]}
              </button>
            ))}
          </div>
          <label className="text-[12px] text-[var(--muted)]">
            Month
            <select value={month} onChange={(e) => { setMonth(e.target.value); setWeek(0); }} className="ml-2 h-8 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] px-2 text-[13px] text-[var(--ink)]">
              {(idx.task_a[source]?.months ?? []).map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
            </select>
          </label>
          <label className="text-[12px] text-[var(--muted)]">
            Zone
            <select value={zone} onChange={(e) => setZone(Number(e.target.value))} className="ml-2 h-8 max-w-[260px] rounded-lg border border-[var(--border)] bg-[var(--surface-2)] px-2 text-[13px] text-[var(--ink)]">
              <option value={-1}>Citywide (sum of zones)</option>
              {[...meta.zones].sort((a, b) => a.zone.localeCompare(b.zone)).map((z) => <option key={z.id} value={z.id}>{z.zone} — {z.borough}</option>)}
            </select>
          </label>
          <div className="ml-auto flex items-center gap-1">
            <button type="button" className="h-8 px-2 rounded-lg border border-[var(--border)] text-[13px] disabled:opacity-40" disabled={week === 0} onClick={() => setWeek(week - 1)} aria-label="Previous week">←</button>
            <span className="text-[12px] text-[var(--ink-2)] tabular px-1">{weekDays.length ? `${dayLabel(weekDays[0])} – ${dayLabel(weekDays[weekDays.length - 1])}` : ""}</span>
            <button type="button" className="h-8 px-2 rounded-lg border border-[var(--border)] text-[13px] disabled:opacity-40" disabled={week >= weeks - 1} onClick={() => setWeek(week + 1)} aria-label="Next week">→</button>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-4 text-[12px] text-[var(--ink-2)]" aria-hidden="true">
          <span className="inline-flex items-center gap-1.5"><span className="h-[2px] w-4 bg-[var(--ink)]" />Observed</span>
          <span className="inline-flex items-center gap-1.5"><span className="h-0 w-4 border-t-2 border-dashed" style={{ borderColor: `var(--series-${source})` }} />Day-ahead forecast</span>
          {zone !== -1 && <span className="inline-flex items-center gap-1.5"><span className="h-3 w-4 rounded-sm opacity-30" style={{ background: `var(--series-${source})` }} />90% interval</span>}
          {zone === -1 && <span className="text-[var(--muted)]">Intervals are calibrated per zone and are not shown for the citywide sum.</span>}
        </div>
        <div ref={chartRef} className="mt-1 w-full" />
      </section>

      {metrics && (
        <section className="panel p-4 overflow-x-auto">
          <h2 className="font-medium">How good is it? Mean over {folds.length} quarterly folds ({folds.join(", ")})</h2>
          <p className="text-[12px] text-[var(--muted)] mt-1 max-w-3xl">
            WAPE = total absolute error ÷ total pickups (lower is better). Zones are grouped by recent volume; “sparse” zones (under 0.5 pickups/hour) are scored by MAE because their WAPE divides by almost nothing. DST-split hours are excluded from scoring.
          </p>
          <table className="mt-3 w-full text-[12px] tabular min-w-[640px]">
            <thead className="text-[var(--muted)]">
              <tr>
                <th className="text-left font-normal py-1">Model</th>
                {WAPE_SEGMENTS.map((s) => <th key={s} className="text-right font-normal">WAPE · {s}</th>)}
                <th className="text-right font-normal">MAE · sparse</th>
                <th className="text-right font-normal">Peak-hour MAE</th>
                <th className="text-right font-normal">90% coverage</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => {
                const w = avg(m, "all", "wape");
                const isBest = w !== null && w === Math.min(...models.map((x) => avg(x, "all", "wape") ?? Infinity));
                return (
                  <tr key={m} className="border-t border-[var(--hairline)]">
                    <td className="py-1.5 pr-2">{MODEL_LABELS[m] ?? m}{isBest && <span className="ml-1.5 text-[11px] text-[var(--success-text)]">best</span>}</td>
                    {WAPE_SEGMENTS.map((s) => <td key={s} className="text-right">{pct(avg(m, s, "wape"))}</td>)}
                    <td className="text-right">{avg(m, "sparse", "mae")?.toFixed(3) ?? "—"}</td>
                    <td className="text-right">{avg(m, "all", "peak_mae")?.toFixed(2) ?? "—"}</td>
                    <td className="text-right">{pct(avg(m, "all", "coverage"))}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {isFinite(baselineBest) && avg("gbm", "all", "wape") !== null && (
            <p className="mt-3 text-[13px] text-[var(--ink-2)]">
              Gradient boosting reduces citywide WAPE by <strong className="text-[var(--ink)]">{((1 - avg("gbm", "all", "wape")! / baselineBest) * 100).toFixed(0)}%</strong> versus the best baseline for {NAMES[source]}.
              {avg("gbm_weather", "all", "wape") !== null && (avg("gbm_weather", "all", "wape")! >= avg("gbm", "all", "wape")! ? " Adding 48-hour-old weather forecasts did not improve it." : " Weather forecasts improved it slightly.")}
            </p>
          )}
        </section>
      )}

      <p className="text-[12px] text-[var(--muted)]">
        Weather inputs: <a className="underline" href="https://open-meteo.com/">Weather data by Open-Meteo.com</a>, licensed <a className="underline" href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>; Previous Runs API (GFS, values forecast ≥48 h ahead), used unmodified as model inputs.
        Trip data: NYC TLC. Model: scikit-learn histogram gradient boosting (Poisson loss), split-conformal 90% intervals.
      </p>

      <section className="panel p-4">
        <h2 className="font-medium">Live track record (lag-aware)</h2>
        <p className="text-[12px] text-[var(--muted)] mt-1 max-w-3xl">
          When TLC publishes a month, Curbside forecasts the next, still-unpublished month from public data only, freezes the file with a SHA-256 hash and commits it. It is scored when TLC publishes that month.
          Method: each zone's average week over the last 8 weeks (holidays use the Sunday profile).
        </p>
        <ul className="mt-3 space-y-2 text-[13px]">
          {live.map((l) => (
            <li key={l.target_month} className="flex flex-wrap gap-x-4 gap-y-1">
              <span className="font-medium">{monthLabel(l.target_month)}</span>
              <span className="text-[var(--ink-2)]">issued {new Date(l.issued_at_utc).toLocaleDateString("en-US")}, data through {new Date(l.information_cutoff_utc).toLocaleDateString("en-US", { timeZone: "UTC" })}</span>
              {idx.task_b_scored?.[l.target_month]?.[source] ? (
                <span>WAPE {pct(idx.task_b_scored[l.target_month][source].all.wape)} · coverage {pct(idx.task_b_scored[l.target_month][source].all.coverage)}</span>
              ) : (
                <span className="text-[var(--muted)]">awaiting TLC publication</span>
              )}
              <code className="text-[11px] text-[var(--muted)]">sha256 {l.sha256.slice(0, 12)}…</code>
            </li>
          ))}
          {!live.length && <li className="text-[var(--muted)]">No frozen forecasts yet.</li>}
        </ul>
        {bt && (
          <table className="mt-4 w-full text-[12px] tabular">
            <caption className="text-left text-[var(--muted)] pb-1">Backtest of the same method (each month forecast from the months before it)</caption>
            <thead className="text-[var(--muted)]"><tr><th className="text-left font-normal">Month</th><th className="text-right font-normal">8-week profile WAPE</th><th className="text-right font-normal">Same week last year WAPE</th></tr></thead>
            <tbody>
              {Object.entries(bt.months).map(([m, v]) => (
                <tr key={m} className="border-t border-[var(--hairline)]">
                  <td>{monthLabel(m)}</td>
                  <td className="text-right">{pct(v.profile_8w?.all?.wape)}</td>
                  <td className="text-right">{pct(v.snaive_52w?.all?.wape)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
