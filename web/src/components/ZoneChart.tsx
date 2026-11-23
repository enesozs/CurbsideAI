import { useEffect, useRef } from "react";
import * as Plot from "@observablehq/plot";
import { a11yPlot } from "../lib/plot";
import type { Source } from "../lib/data";

export interface SeriesPoint {
  t: Date;
  v: number;
  source: Source;
}

interface Props {
  data: SeriesPoint[];
  current: Date | null;
  yLabel: string;
  height?: number;
  ariaLabel: string;
}

const NAMES: Record<Source, string> = { yellow: "Yellow taxi", hvfhv: "Uber/Lyft" };

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function ZoneChart({ data, current, yLabel, height = 170, ariaLabel }: Props) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const width = el.clientWidth || 320;
    const sources = [...new Set(data.map((d) => d.source))] as Source[];
    const color = { yellow: cssVar("--series-yellow"), hvfhv: cssVar("--series-hvfhv") };
    const ink2 = cssVar("--ink-2");
    const muted = cssVar("--muted");
    const grid = cssVar("--hairline");
    const plot = Plot.plot({
      width,
      height,
      marginLeft: 40,
      marginRight: 8,
      marginTop: 8,
      marginBottom: 24,
      style: { background: "transparent", color: muted, fontSize: "10px", fontFamily: "inherit" },
      x: { type: "time", tickFormat: "%a %-d", ticks: 7, label: null },
      y: { grid: true, label: null, tickFormat: "~s", nice: true, zero: true },
      marks: [
        Plot.gridY({ stroke: grid, strokeOpacity: 1 }),
        Plot.ruleY([0], { stroke: grid }),
        ...(current ? [Plot.ruleX([current], { stroke: ink2, strokeWidth: 1, strokeDasharray: "2,2" })] : []),
        Plot.line(data, {
          x: "t",
          y: "v",
          z: "source",
          stroke: (d: SeriesPoint) => color[d.source],
          strokeWidth: 2,
          curve: "monotone-x",
        }),
        Plot.ruleX(
          data,
          Plot.pointerX({ x: "t", stroke: muted, strokeOpacity: 0.6 }),
        ),
        Plot.dot(
          data,
          Plot.pointerX({ x: "t", y: "v", fill: (d: SeriesPoint) => color[d.source], r: 4, stroke: cssVar("--surface"), strokeWidth: 2 }),
        ),
        Plot.tip(
          data,
          Plot.pointerX({
            x: "t",
            y: "v",
            title: (d: SeriesPoint) =>
              `${NAMES[d.source]}\n${d.t.toLocaleString("en-US", { timeZone: "America/New_York", weekday: "short", hour: "numeric", month: "short", day: "numeric" })}\n${Math.round(d.v).toLocaleString("en-US")} ${yLabel}`,
            fill: cssVar("--surface"),
            stroke: cssVar("--border"),
          }),
        ),
      ],
    });
    a11yPlot(plot, ariaLabel);
    el.replaceChildren(plot);
    void sources;
    return () => plot.remove();
  }, [data, current, yLabel, height, ariaLabel]);

  const sources = [...new Set(data.map((d) => d.source))] as Source[];
  return (
    <div>
      {sources.length > 1 && (
        <div className="flex gap-3 text-[11px] text-[var(--ink-2)] mb-1" aria-hidden="true">
          {sources.map((s) => (
            <span key={s} className="inline-flex items-center gap-1.5">
              <span className="h-[3px] w-3 rounded" style={{ background: `var(--series-${s})` }} />
              {NAMES[s]}
            </span>
          ))}
        </div>
      )}
      <div ref={ref} className="w-full" />
    </div>
  );
}
