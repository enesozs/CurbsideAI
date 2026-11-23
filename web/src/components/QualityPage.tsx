import { useEffect, useRef, useState } from "react";
import * as Plot from "@observablehq/plot";
import { a11yPlot } from "../lib/plot";
import type { Meta, Source } from "../lib/data";
import { monthLabel } from "../lib/time";

interface MonthRecon {
  month: string;
  aggregate_total: number;
  our_total: number;
  rel_diff: number | null;
}
interface SourceQA {
  months: number;
  raw_rows_total: number;
  gold_trip_total: number;
  flag_totals: Record<string, number>;
  gold_invariants_pass: boolean;
  schema_versions: { months: string[]; n_columns: number }[];
  reconciliation: { rule: string; tolerance: number; per_month: MonthRecon[] };
  dst_checks: { date: string; kind: string; raw_pickups_in_wall_clock_hour: number; reference_median: number; ratio: number | null }[];
  per_month: { month: string; raw_rows: number; counted_rows: number; gold_trip_total: number; catchall_pickups: number }[];
}
interface QA {
  generated_at: string;
  window: [string, string];
  sources: Record<Source, SourceQA>;
}

const NAMES: Record<Source, string> = { yellow: "Yellow taxi", hvfhv: "Uber/Lyft" };
const FLAG_HELP: Record<string, string> = {
  negative_amount: "Negative fare total (reversal record) — not counted",
  negative_duration: "Drop-off before pickup — counted, excluded from duration stats",
  duration_over_24h: "Trip longer than 24 h — counted, excluded from stats",
  non_positive_distance: "Zero or negative distance — counted, excluded from stats",
  pickup_far_outside_file_month: "Pickup >1 day outside the file's month — not counted",
  pickup_outside_file_month: "Pickup just outside the file's month — counted in the right hour",
  dst_nonexistent: "Stamped in the skipped spring-forward hour — moved +1 h",
  unknown_zone: "Zone ID not in the official lookup",
  catchall_pickup_zone: "Pickup in zone 264/265 (unknown / outside NYC) — not mapped",
  wait_invalid: "Wait time missing, negative or over 1 h — excluded from wait medians",
  null_pickup: "Missing pickup time — not counted",
};

function cssVar(n: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim();
}

function ReconChart({ qa, theme }: { qa: QA; theme: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const rows = (Object.keys(qa.sources) as Source[]).flatMap((s) =>
      qa.sources[s].reconciliation.per_month
        .filter((m) => m.rel_diff !== null)
        .map((m) => ({ source: s, t: new Date(`${m.month}-15T12:00:00Z`), d: (m.rel_diff as number) * 100, month: m.month })),
    );
    const tol = qa.sources.yellow?.reconciliation.tolerance ?? 0.005;
    const plot = Plot.plot({
      width: el.clientWidth || 640,
      height: 240,
      marginLeft: 44,
      marginBottom: 36,
      insetBottom: 10,
      style: { background: "transparent", color: cssVar("--muted"), fontSize: "11px", fontFamily: "inherit" },
      x: { type: "time", label: null },
      y: { label: "Difference vs TLC aggregate (%)", grid: true, nice: true, tickFormat: (d: number) => `${d > 0 ? "+" : ""}${d}` },
      marks: [
        Plot.gridY({ stroke: cssVar("--hairline"), strokeOpacity: 1 }),
        Plot.rectY([0], { x1: rows[0]?.t, x2: rows[rows.length - 1]?.t, y1: -tol * 100, y2: tol * 100, fill: cssVar("--status-good"), fillOpacity: 0.12 }),
        Plot.ruleY([0], { stroke: cssVar("--ink-2") }),
        Plot.dot(rows, { x: "t", y: "d", fill: (r: { source: Source }) => cssVar(`--series-${r.source}`), r: 3.5, stroke: cssVar("--surface"), strokeWidth: 1 }),
        Plot.tip(rows, Plot.pointer({ x: "t", y: "d", title: (r: { source: Source; month: string; d: number }) => `${NAMES[r.source]} · ${monthLabel(r.month)}\n${r.d > 0 ? "+" : ""}${r.d.toFixed(2)}% vs TLC aggregate`, fill: cssVar("--surface") })),
      ],
    });
    a11yPlot(plot, "Monthly difference between Curbside pickup counts and TLC's published monthly aggregate, per source");
    el.replaceChildren(plot);
    return () => plot.remove();
  }, [qa, theme]);
  return <div ref={ref} className="w-full" />;
}

export function QualityPage({ meta, theme }: { meta: Meta; theme: string }) {
  const [qa, setQa] = useState<QA | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    fetch("/data/qa.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setQa)
      .catch((e) => setErr(String(e.message || e)));
  }, []);

  if (err) return <div className="p-8 text-[var(--ink-2)]">The quality report is not available ({err}). Run <code>curbside qa</code> and <code>curbside build-web</code>.</div>;
  if (!qa) return <div className="p-8 text-[var(--ink-2)]">Loading quality report…</div>;

  const sources = Object.keys(qa.sources) as Source[];
  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6 py-8 space-y-8">
      <header>
        <h1 className="font-display text-[32px] leading-tight">Data quality</h1>
        <p className="mt-2 text-[var(--ink-2)] max-w-3xl">
          Generated by the pipeline on {new Date(qa.generated_at).toLocaleString("en-US")} for {monthLabel(qa.window[0])} – {monthLabel(qa.window[1])}. Every
          raw row is kept in the validated layer with flags; the counts on the map apply the rules below. Nothing here is edited by hand.
        </p>
      </header>

      <section className="grid sm:grid-cols-2 gap-4">
        {sources.map((s) => {
          const q = qa.sources[s];
          return (
            <div key={s} className="panel p-4">
              <div className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: `var(--series-${s})` }} />
                <h2 className="font-medium">{NAMES[s]}</h2>
                <span className={`ml-auto text-[12px] inline-flex items-center gap-1 ${q.gold_invariants_pass ? "" : "text-[var(--status-critical)]"}`}>
                  <span aria-hidden="true">{q.gold_invariants_pass ? "✓" : "✕"}</span> invariants {q.gold_invariants_pass ? "pass" : "fail"}
                </span>
              </div>
              <dl className="mt-3 grid grid-cols-3 gap-2 text-[12px]">
                <div><dt className="text-[var(--muted)]">Months</dt><dd className="tabular text-[18px] font-display">{q.months}</dd></div>
                <div><dt className="text-[var(--muted)]">Raw rows</dt><dd className="tabular text-[18px] font-display">{(q.raw_rows_total / 1e6).toFixed(1)}M</dd></div>
                <div><dt className="text-[var(--muted)]">Pickups mapped</dt><dd className="tabular text-[18px] font-display">{(q.gold_trip_total / 1e6).toFixed(1)}M</dd></div>
              </dl>
              <div className="mt-3 text-[12px] text-[var(--muted)]">
                Schema versions: {q.schema_versions.map((v) => `${v.n_columns} columns (${v.months[0]}${v.months.length > 1 ? ` → ${v.months[v.months.length - 1]}` : ""})`).join(" · ")}
              </div>
            </div>
          );
        })}
      </section>

      <section className="panel p-4">
        <h2 className="font-medium">Reconciliation with TLC's own monthly totals</h2>
        <p className="text-[12px] text-[var(--muted)] mt-1 max-w-3xl">
          Monthly pickups per zone compared with NYC Open Data “Pickups and Drop-offs by Taxi Zone and Industry” (c5iv-bn4s). Shaded band: ±{(qa.sources.yellow?.reconciliation.tolerance ?? 0.005) * 100}% tolerance.
          Rule in use — yellow: <code>{qa.sources.yellow?.reconciliation.rule}</code>{qa.sources.hvfhv ? <>; Uber/Lyft: <code>{qa.sources.hvfhv.reconciliation.rule}</code></> : null}. Months outside the band are
          listed below; they are shown on the map as published by TLC and flagged here rather than adjusted.
        </p>
        <div className="mt-3 flex gap-4 text-[12px] text-[var(--ink-2)]">
          {sources.map((s) => (
            <span key={s} className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-full" style={{ background: `var(--series-${s})` }} />{NAMES[s]}</span>
          ))}
        </div>
        <ReconChart qa={qa} theme={theme} />
        <table className="mt-3 w-full text-[12px] tabular">
          <caption className="text-left text-[var(--muted)] pb-1">Months outside tolerance</caption>
          <thead className="text-[var(--muted)]"><tr><th className="text-left font-normal">Source</th><th className="text-left font-normal">Month</th><th className="text-right font-normal">TLC aggregate</th><th className="text-right font-normal">Curbside</th><th className="text-right font-normal">Difference</th></tr></thead>
          <tbody>
            {sources.flatMap((s) =>
              qa.sources[s].reconciliation.per_month
                .filter((m) => m.rel_diff !== null && Math.abs(m.rel_diff) > qa.sources[s].reconciliation.tolerance)
                .map((m) => (
                  <tr key={s + m.month} className="border-t border-[var(--hairline)]">
                    <td>{NAMES[s]}</td><td>{monthLabel(m.month)}</td>
                    <td className="text-right">{m.aggregate_total.toLocaleString("en-US")}</td>
                    <td className="text-right">{m.our_total.toLocaleString("en-US")}</td>
                    <td className="text-right">{m.rel_diff! > 0 ? "+" : ""}{(m.rel_diff! * 100).toFixed(2)}%</td>
                  </tr>
                )),
            )}
          </tbody>
        </table>
      </section>

      <section className="panel p-4">
        <h2 className="font-medium">Validation flags (all months)</h2>
        <table className="mt-2 w-full text-[12px] tabular">
          <thead className="text-[var(--muted)]"><tr><th className="text-left font-normal">Rule</th>{sources.map((s) => <th key={s} className="text-right font-normal">{NAMES[s]}</th>)}</tr></thead>
          <tbody>
            {Object.keys(FLAG_HELP).map((f) => (
              <tr key={f} className="border-t border-[var(--hairline)]">
                <td className="py-1 pr-2">{FLAG_HELP[f]}</td>
                {sources.map((s) => <td key={s} className="text-right">{(qa.sources[s].flag_totals[f] ?? 0).toLocaleString("en-US")}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel p-4">
        <h2 className="font-medium">Daylight-saving checks</h2>
        <p className="text-[12px] text-[var(--muted)] mt-1 max-w-3xl">
          TLC timestamps are New York wall-clock times. Expect ~0 pickups in the skipped spring hour and ~2× normal in the repeated fall hour (two real hours share one label; Curbside splits them evenly).
        </p>
        <table className="mt-2 w-full text-[12px] tabular">
          <thead className="text-[var(--muted)]"><tr><th className="text-left font-normal">Source</th><th className="text-left font-normal">Date</th><th className="text-left font-normal">Transition</th><th className="text-right font-normal">Pickups in hour</th><th className="text-right font-normal">Typical</th><th className="text-right font-normal">Ratio</th></tr></thead>
          <tbody>
            {sources.flatMap((s) =>
              qa.sources[s].dst_checks.map((d) => (
                <tr key={s + d.date} className="border-t border-[var(--hairline)]">
                  <td>{NAMES[s]}</td><td>{d.date}</td><td>{d.kind === "spring_forward" ? "Spring forward (2 AM skipped)" : "Fall back (1 AM repeated)"}</td>
                  <td className="text-right">{d.raw_pickups_in_wall_clock_hour.toLocaleString("en-US")}</td>
                  <td className="text-right">{Math.round(d.reference_median).toLocaleString("en-US")}</td>
                  <td className="text-right">{d.ratio === null ? "—" : `${d.ratio.toFixed(2)}×`}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      </section>

      <p className="text-[12px] text-[var(--muted)]">Artifacts generated {new Date(meta.generated_at).toLocaleString("en-US")}.</p>
    </div>
  );
}
