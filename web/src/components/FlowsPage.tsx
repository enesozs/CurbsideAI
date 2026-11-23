import { useEffect, useMemo, useRef, useState } from "react";
import maplibregl, { type GeoJSONSource } from "maplibre-gl";
import * as Plot from "@observablehq/plot";
import { a11yPlot } from "../lib/plot";
import type { Meta, Source } from "../lib/data";
import { monthLabel } from "../lib/time";
import { basemapStyle } from "../lib/basemap";
import type { Theme } from "../lib/scales";

const NAMES: Record<Source, string> = { yellow: "Yellow taxi", hvfhv: "Uber/Lyft" };

interface FlowIndex {
  min_trips: number;
  outside_index: number;
  boroughs: string[];
  sources: Record<Source, Record<string, { days: number; weekday_days: number; weekend_days: number; pairs: number; total_trips: number; kept_trips: number; borough_matrix: number[][] }>>;
}

interface Pair {
  o: number;
  d: number;
  wd: number;
  we: number;
  min: number;
}

async function loadPairs(source: Source, month: string): Promise<Pair[]> {
  const r = await fetch(`/data/flows/${source}/${month}.bin`);
  if (!r.ok) throw new Error(`flows ${month}: HTTP ${r.status}`);
  const dv = new DataView(await r.arrayBuffer());
  const out: Pair[] = [];
  for (let off = 0; off + 16 <= dv.byteLength; off += 16) {
    out.push({
      o: dv.getUint16(off, true),
      d: dv.getUint16(off + 2, true),
      wd: dv.getUint32(off + 4, true),
      we: dv.getUint32(off + 8, true),
      min: dv.getUint16(off + 12, true) / 10,
    });
  }
  return out;
}

/** Quadratic curve between two points, bending to the right of travel so A→B and B→A separate. */
function arc(a: [number, number], b: [number, number], n = 24): [number, number][] {
  const mx = (a[0] + b[0]) / 2;
  const my = (a[1] + b[1]) / 2;
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const k = 0.18;
  const c: [number, number] = [mx + dy * k, my - dx * k];
  const pts: [number, number][] = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    pts.push([
      (1 - t) * (1 - t) * a[0] + 2 * (1 - t) * t * c[0] + t * t * b[0],
      (1 - t) * (1 - t) * a[1] + 2 * (1 - t) * t * c[1] + t * t * b[1],
    ]);
  }
  return pts;
}

function cssVar(n: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(n).trim();
}

export function FlowsPage({ meta, theme }: { meta: Meta; theme: Theme }) {
  const [idx, setIdx] = useState<FlowIndex | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [source, setSource] = useState<Source>("hvfhv");
  const [month, setMonth] = useState("");
  const [dayType, setDayType] = useState<"wd" | "we">("wd");
  const [topN, setTopN] = useState(150);
  const [focus, setFocus] = useState<number | null>(null); // zone column index
  const [direction, setDirection] = useState<"from" | "to">("from");
  const [pairs, setPairs] = useState<Pair[] | null>(null);
  const mapEl = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const matrixRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/data/flows/index.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: FlowIndex) => {
        setIdx(d);
        const ms = Object.keys(d.sources.hvfhv ?? {}).sort();
        setMonth(ms[ms.length - 1] ?? "");
      })
      .catch((e) => setErr(String(e.message || e)));
  }, []);

  useEffect(() => {
    if (!month) return;
    let alive = true;
    loadPairs(source, month)
      .then((p) => alive && setPairs(p))
      .catch((e) => alive && setErr(String(e.message || e)));
    return () => {
      alive = false;
    };
  }, [source, month]);

  const mi = idx?.sources[source]?.[month];
  const nDays = mi ? (dayType === "wd" ? mi.weekday_days : mi.weekend_days) : 1;
  const outside = idx?.outside_index ?? meta.zones.length;
  const zoneName = (i: number) => (i === outside ? "Outside NYC" : meta.zones[i]?.zone ?? "?");

  const shown = useMemo(() => {
    if (!pairs) return [];
    const perDay = pairs
      .map((p) => ({ ...p, v: (dayType === "wd" ? p.wd : p.we) / Math.max(1, nDays) }))
      .filter((p) => p.v > 0 && p.o !== p.d && (focus === null || (direction === "from" ? p.o === focus : p.d === focus)));
    perDay.sort((a, b) => b.v - a.v);
    return perDay.slice(0, topN);
  }, [pairs, dayType, nDays, focus, direction, topN]);

  // Map: zones + arcs. Created once the page has rendered its container (after the index loads).
  const loaded = idx !== null;
  useEffect(() => {
    if (!mapEl.current) return;
    setMapReady(false);
    const map = new maplibregl.Map({
      container: mapEl.current,
      style: basemapStyle(theme),
      bounds: [-74.12, 40.56, -73.72, 40.9],
      minZoom: 9,
      maxZoom: 15,
      attributionControl: { compact: true },
      dragRotate: false,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    mapRef.current = map;
    map.on("load", () => {
      const labels = map.getLayer("address_label") ? "address_label" : undefined;
      map.addSource("zones", { type: "geojson", data: "/data/zones.geojson", promoteId: "id" });
      map.addLayer({ id: "z-fill", type: "fill", source: "zones", paint: { "fill-color": theme === "dark" ? "#232321" : "#f2f1ed", "fill-opacity": 0.75 } }, labels);
      map.addLayer({ id: "z-focus", type: "fill", source: "zones", filter: ["==", ["get", "id"], -1], paint: { "fill-color": theme === "dark" ? "#3987e5" : "#256abf", "fill-opacity": 0.35 } }, labels);
      map.addLayer({ id: "z-line", type: "line", source: "zones", paint: { "line-color": theme === "dark" ? "rgba(255,255,255,0.18)" : "rgba(11,11,11,0.18)", "line-width": 0.5 } }, labels);
      map.addSource("arcs", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({
        id: "arcs",
        type: "line",
        source: "arcs",
        layout: { "line-cap": "round" },
        paint: { "line-color": ["get", "color"], "line-width": ["get", "w"], "line-opacity": 0.55 },
      });
      map.on("click", "z-fill", (e) => {
        const id = Number(e.features?.[0]?.id);
        const zi = meta.zones.findIndex((z) => z.id === id);
        setFocus((cur) => (cur === zi ? null : zi));
      });
      map.on("mouseenter", "z-fill", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "z-fill", () => (map.getCanvas().style.cursor = ""));
      setMapReady(true);
    });
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [theme, meta, loaded]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const color = cssVar(`--series-${source}`);
    const max = shown.reduce((m, p) => Math.max(m, p.v), 1);
    const features = shown
      .filter((p) => p.o !== outside && p.d !== outside)
      .map((p) => ({
        type: "Feature" as const,
        properties: { color, w: 0.6 + 6 * Math.sqrt(p.v / max) },
        geometry: { type: "LineString" as const, coordinates: arc(meta.zones[p.o].label, meta.zones[p.d].label) },
      }));
    (map.getSource("arcs") as GeoJSONSource).setData({ type: "FeatureCollection", features });
    map.setFilter("z-focus", ["==", ["get", "id"], focus === null ? -1 : meta.zones[focus].id]);
  }, [shown, mapReady, source, focus, meta, outside]);

  // Borough matrix (exact, all trips) as trips/day.
  useEffect(() => {
    const el = matrixRef.current;
    if (!el || !mi || !idx) return;
    const days = mi.days;
    const cells = idx.boroughs.flatMap((o, i) =>
      idx.boroughs.map((d, j) => ({ o, d, v: mi.borough_matrix[i][j] / days })),
    );
    const plot = Plot.plot({
      width: Math.min(el.clientWidth || 560, 640),
      height: 300,
      marginLeft: 100,
      marginBottom: 70,
      padding: 0.04,
      style: { background: "transparent", color: cssVar("--muted"), fontSize: "11px", fontFamily: "inherit" },
      x: { domain: idx.boroughs, label: "Drop-off borough →", tickRotate: -30 },
      y: { domain: idx.boroughs.slice(0, 6), label: "Pickup borough" },
      color: { type: "log", scheme: "blues", reverse: theme === "dark" },
      marks: [
        Plot.cell(cells.filter((c) => c.o !== "Outside NYC" && c.v > 0), { x: "d", y: "o", fill: "v", inset: 1, rx: 3 }),
        Plot.text(cells.filter((c) => c.o !== "Outside NYC" && c.v > 0), {
          x: "d",
          y: "o",
          text: (c: { v: number }) => (c.v >= 1000 ? `${(c.v / 1000).toFixed(c.v >= 10000 ? 0 : 1)}k` : c.v >= 10 ? Math.round(c.v).toString() : c.v.toFixed(1)),
          fill: (c: { v: number }) => (c.v > 3000 ? "white" : cssVar("--ink")),
          fontSize: 10,
        }),
      ],
    });
    a11yPlot(plot, "Average trips per day between boroughs");
    el.replaceChildren(plot);
    return () => plot.remove();
  }, [mi, idx, theme]);

  if (err) return <div className="p-8 text-[var(--ink-2)]">Flow artifacts are not available ({err}).</div>;
  if (!idx) return <div className="p-8 text-[var(--ink-2)]">Loading flows…</div>;
  const months = Object.keys(idx.sources[source] ?? {}).sort();

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-8 space-y-6">
      <header>
        <h1 className="font-display text-[32px] leading-tight">Flows</h1>
        <p className="mt-2 text-[var(--ink-2)] max-w-3xl">
          Where trips go: average completed trips per day between taxi zones. Lines are <strong className="text-[var(--ink)]">schematic links between zone centres, not street routes</strong>; each bends to the right of its direction of travel, so A→B and B→A separate. Click a zone to see its flows.
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
          <div className="seg" role="group" aria-label="Day type">
            <button type="button" aria-pressed={dayType === "wd"} onClick={() => setDayType("wd")}>Weekdays</button>
            <button type="button" aria-pressed={dayType === "we"} onClick={() => setDayType("we")}>Weekends</button>
          </div>
          <label className="text-[12px] text-[var(--muted)]">
            Month
            <select value={month} onChange={(e) => setMonth(e.target.value)} className="ml-2 h-8 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] px-2 text-[13px] text-[var(--ink)]">
              {months.map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
            </select>
          </label>
          <label className="text-[12px] text-[var(--muted)] flex items-center gap-2">
            Show top
            <input type="range" min={25} max={500} step={25} value={topN} onChange={(e) => setTopN(Number(e.target.value))} aria-valuetext={`${topN} links`} />
            <span className="tabular text-[var(--ink)] w-8">{topN}</span>
          </label>
          {focus !== null && (
            <div className="flex items-center gap-2 ml-auto">
              <div className="seg" role="group" aria-label="Direction">
                <button type="button" aria-pressed={direction === "from"} onClick={() => setDirection("from")}>From</button>
                <button type="button" aria-pressed={direction === "to"} onClick={() => setDirection("to")}>To</button>
              </div>
              <span className="text-[13px] font-medium">{zoneName(focus)}</span>
              <button type="button" className="text-[12px] text-[var(--ink-2)] px-2 py-1 rounded-md hover:bg-[var(--surface-2)]" onClick={() => setFocus(null)}>Clear</button>
            </div>
          )}
        </div>
      </section>

      <div className="grid lg:grid-cols-[1fr_340px] gap-4">
        <div className="panel overflow-hidden relative h-[520px]">
          <div ref={mapEl} className="h-full w-full" aria-label="Map of schematic flow links between taxi zones" role="region" />
          <div className="absolute left-3 bottom-3 panel px-3 py-2 text-[11px] text-[var(--ink-2)] max-w-[260px]">
            Line width ∝ √(trips/day) · {dayType === "wd" ? "weekday" : "weekend"} average · {monthLabel(month)} · observed
          </div>
        </div>
        <div className="panel p-0 overflow-hidden flex flex-col max-h-[520px]">
          <div className="px-4 pt-3 pb-2 border-b border-[var(--hairline)]">
            <div className="text-[13px] font-medium">Top links · trips per {dayType === "wd" ? "weekday" : "weekend day"}</div>
            <div className="text-[11px] text-[var(--muted)]">Typical time: trip-weighted mean of daily median durations</div>
          </div>
          <div className="overflow-auto" tabIndex={0} aria-label="Top links table">
            <table className="w-full text-[12px] tabular">
              <thead className="sticky top-0 bg-[var(--surface)] text-[var(--muted)]">
                <tr><th className="text-left font-normal px-4 py-1">From → to</th><th className="text-right font-normal px-2">Trips</th><th className="text-right font-normal px-4">Min</th></tr>
              </thead>
              <tbody>
                {shown.slice(0, 100).map((p) => (
                  <tr key={`${p.o}-${p.d}`} className="border-t border-[var(--hairline)]">
                    <td className="px-4 py-1">{zoneName(p.o)} <span className="text-[var(--muted)]">→</span> {zoneName(p.d)}</td>
                    <td className="text-right px-2">{p.v >= 100 ? Math.round(p.v).toLocaleString("en-US") : p.v.toFixed(1)}</td>
                    <td className="text-right px-4 text-[var(--ink-2)]">{p.min ? p.min.toFixed(0) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <section className="panel p-4">
        <h2 className="font-medium">Between boroughs · average trips per day, all days, {monthLabel(month)}</h2>
        <p className="text-[12px] text-[var(--muted)] mt-1">Exact totals including links below the map threshold ({mi ? ((mi.kept_trips / mi.total_trips) * 100).toFixed(1) : "—"}% of trips are on links shown above the {idx.min_trips}-trip threshold). Rows: pickup borough; columns: drop-off borough.</p>
        <div ref={matrixRef} className="mt-2 w-full overflow-x-auto" />
      </section>
    </div>
  );
}
