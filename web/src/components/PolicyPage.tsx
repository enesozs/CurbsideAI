import { useEffect, useRef, useState } from "react";
import * as Plot from "@observablehq/plot";
import { a11yPlot } from "../lib/plot";
import type { Source } from "../lib/data";

const NAMES: Record<Source, string> = { yellow: "Yellow taxi", hvfhv: "Uber/Lyft" };
const GROUPS: Record<string, string> = {
  treated: "Both ends inside the zone",
  adjacent: "Manhattan, outside the zone",
  control: "Outside Manhattan",
};

interface Est { att_pct: number; ci_pct: [number, number]; pairs?: number }
interface SourceStudy {
  pace_by_month: { group: string; month: string; min_per_mile: number; trips: number }[];
  entry_flows: { month: string; relation: string; trips_per_day: number }[];
  did_seasonal: Est;
  did_seasonal_adjacent_control: Est;
  placebo_2024: Est;
  did_trend: Est & { trend_pct_per_year: number };
  event_study_naive: { pretrend_p: number };
  verdict: { placebo_passes: boolean; credible_causal_estimate: boolean; summary: string };
}
interface Study { policy_start: string; crz: { zones: number[]; rule: string }; sources: Record<Source, SourceStudy> }

function cssVar(n: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim();
}

const GROUP_COLORS = () => ({ treated: cssVar("--accent"), adjacent: cssVar("--muted"), control: cssVar("--ink") });

function PaceChart({ s, theme }: { s: SourceStudy; theme: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const data = s.pace_by_month.map((d) => ({ ...d, t: new Date(`${d.month}-15T12:00:00Z`) }));
    const c = GROUP_COLORS();
    const plot = Plot.plot({
      width: el.clientWidth || 520,
      height: 240,
      marginLeft: 44,
      style: { background: "transparent", color: cssVar("--muted"), fontSize: "11px", fontFamily: "inherit" },
      x: { type: "time", label: null },
      y: { label: "minutes per mile (median trip pace)", grid: true, nice: true },
      marks: [
        Plot.gridY({ stroke: cssVar("--hairline"), strokeOpacity: 1 }),
        Plot.ruleX([new Date("2025-01-05T12:00:00Z")], { stroke: cssVar("--ink-2"), strokeDasharray: "3,3" }),
        Plot.text([new Date("2025-01-05T12:00:00Z")], { text: ["tolling starts"], frameAnchor: "top", dx: 4, textAnchor: "start", fill: cssVar("--ink-2") }),
        Plot.line(data, { x: "t", y: "min_per_mile", z: "group", stroke: (d: { group: keyof ReturnType<typeof GROUP_COLORS> }) => c[d.group], strokeWidth: 2 }),
        Plot.tip(data, Plot.pointer({ x: "t", y: "min_per_mile", title: (d: { group: string; month: string; min_per_mile: number }) => `${GROUPS[d.group]}\n${d.month}: ${d.min_per_mile.toFixed(2)} min/mile`, fill: cssVar("--surface") })),
      ],
    });
    a11yPlot(plot, "Monthly median trip pace by group");
    el.replaceChildren(plot);
    return () => plot.remove();
  }, [s, theme]);
  return <div ref={ref} className="w-full" />;
}

function EstimateChart({ s, theme }: { s: SourceStudy; theme: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const rows = [
      { label: "Seasonal DiD (main)", e: s.did_seasonal },
      { label: "With group trend", e: s.did_trend },
      { label: "Control: Manhattan outside zone", e: s.did_seasonal_adjacent_control },
      { label: "Placebo: fake start Jan 2024", e: s.placebo_2024 },
    ].map((r) => ({ label: r.label, v: r.e.att_pct, lo: r.e.ci_pct[0], hi: r.e.ci_pct[1] }));
    const plot = Plot.plot({
      width: el.clientWidth || 520,
      height: 180,
      marginLeft: 210,
      style: { background: "transparent", color: cssVar("--muted"), fontSize: "11px", fontFamily: "inherit" },
      x: { label: "change in trip pace vs control (%) — negative = faster", grid: true, nice: true },
      y: { label: null, domain: rows.map((r) => r.label) },
      marks: [
        Plot.ruleX([0], { stroke: cssVar("--ink-2") }),
        Plot.rectX([0], { x1: -0.5, x2: 0.5, fill: cssVar("--muted"), fillOpacity: 0.1 }),
        Plot.ruleY(rows, { y: "label", x1: "lo", x2: "hi", stroke: cssVar("--ink"), strokeWidth: 2 }),
        Plot.dot(rows, { y: "label", x: "v", r: 4.5, fill: cssVar("--accent"), stroke: cssVar("--surface"), strokeWidth: 1.5 }),
        Plot.text(rows, { y: "label", x: "hi", text: (r: { v: number }) => `${r.v > 0 ? "+" : ""}${r.v.toFixed(1)}%`, dx: 8, textAnchor: "start", fill: cssVar("--ink") }),
      ],
    });
    a11yPlot(plot, "Estimated change in trip pace with 95% confidence intervals");
    el.replaceChildren(plot);
    return () => plot.remove();
  }, [s, theme]);
  return <div ref={ref} className="w-full" />;
}

export function PolicyPage({ theme }: { theme: string }) {
  const [study, setStudy] = useState<Study | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [source, setSource] = useState<Source>("hvfhv");
  useEffect(() => {
    fetch("/data/policy.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setStudy)
      .catch((e) => setErr(String(e.message || e)));
  }, []);
  if (err) return <div className="p-8 text-[var(--ink-2)]">The policy study is not available ({err}).</div>;
  if (!study) return <div className="p-8 text-[var(--ink-2)]">Loading study…</div>;
  const s = study.sources[source];
  const c = GROUP_COLORS();

  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6 py-8 space-y-6">
      <header>
        <h1 className="font-display text-[32px] leading-tight">Congestion pricing: what changed for taxi trips?</h1>
        <p className="mt-2 text-[var(--ink-2)] max-w-3xl">
          Tolling of Manhattan's Central Business District began on January 5, 2025. Using TLC trip records only, we compare how fast trips moved inside the toll zone with comparable trips elsewhere.
          <strong className="text-[var(--ink)]"> Trip pace is not traffic speed</strong>, and a before/after pattern is not proof of cause — the checks below say how far the data can go.
        </p>
      </header>

      <div className="seg" role="group" aria-label="Service">
        {(["yellow", "hvfhv"] as Source[]).map((x) => (
          <button key={x} type="button" aria-pressed={source === x} onClick={() => setSource(x)}>
            <span className="inline-block h-2 w-2 rounded-full mr-1.5 align-middle" style={{ background: `var(--series-${x})` }} />
            {NAMES[x]}
          </button>
        ))}
      </div>

      <section className={`panel p-4 border-l-4`} style={{ borderLeftColor: s.verdict.credible_causal_estimate ? "var(--status-good)" : "var(--status-warning)" }}>
        <h2 className="font-medium flex items-center gap-2">
          <span aria-hidden="true">{s.verdict.credible_causal_estimate ? "✓" : "!"}</span>
          {s.verdict.credible_causal_estimate ? "Placebo check passes" : "Placebo check fails — no causal claim"}
        </h2>
        <p className="mt-1 text-[13px] text-[var(--ink-2)]">{s.verdict.summary}</p>
      </section>

      <section className="panel p-4">
        <h2 className="font-medium">Median trip pace by month · {NAMES[source]}</h2>
        <div className="mt-2 flex flex-wrap gap-4 text-[12px] text-[var(--ink-2)]" aria-hidden="true">
          {Object.entries(GROUPS).map(([k, v]) => (
            <span key={k} className="inline-flex items-center gap-1.5"><span className="h-[3px] w-4 rounded" style={{ background: c[k as keyof typeof c] }} />{v}</span>
          ))}
        </div>
        <PaceChart s={s} theme={theme} />
        <p className="text-[12px] text-[var(--muted)]">Observed · trip-weighted medians over OD pairs · New York months. Higher = slower.</p>
      </section>

      <section className="panel p-4">
        <h2 className="font-medium">Estimates with 95% confidence intervals</h2>
        <p className="text-[12px] text-[var(--muted)] mt-1 max-w-3xl">
          Difference-in-differences on log pace with OD-pair and month fixed effects and group-specific seasonality; standard errors clustered by OD pair. The shaded band (±0.5%) is the margin within which a placebo counts as negligible.
        </p>
        <EstimateChart s={s} theme={theme} />
      </section>

      <section className="panel p-4 text-[13px] text-[var(--ink-2)] space-y-2">
        <h2 className="font-medium text-[var(--ink)]">Method and limits</h2>
        <p>Toll-zone membership of the {study.crz.zones.length} taxi zones is read from TLC's own congestion-fee field ({study.crz.rule}).</p>
        <p>Coincident changes cannot be separated from the policy: return-to-office trends, fare and surcharge changes, roadworks and bus lanes, and the mix of trips (the fee itself changes which trips are taken). Trips deterred by the toll are not in the data.</p>
        <p>The MTA's own vehicle-entry and speed datasets were not used in this version.</p>
      </section>
    </div>
  );
}
