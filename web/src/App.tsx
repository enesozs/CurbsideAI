import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { loadMeta, loadMonth, monthsAvailable, type Meta, type MonthData } from "./lib/data";
import { dayLabel, groupByDay, hourInfos, hourLabel, type HourInfo } from "./lib/time";
import type { Measure, Theme } from "./lib/scales";
import { formatValue, measureUnit, valuesAt, type SourceSel } from "./lib/values";
import { MapView } from "./components/MapView";
import { Legend } from "./components/Legend";
import { TimeControls } from "./components/TimeControls";
import { Inspector } from "./components/Inspector";
import { TableView } from "./components/TableView";
import { QualityPage } from "./components/QualityPage";
import { MethodPage } from "./components/MethodPage";
import { ForecastPage } from "./components/ForecastPage";
import { FlowsPage } from "./components/FlowsPage";
import { PolicyPage } from "./components/PolicyPage";

type View = "pulse" | "flows" | "forecast" | "policy" | "quality" | "method";
const BOROUGHS = ["All", "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "EWR"];
const PLAY_MS = 450;

function initialTheme(): Theme {
  try {
    const t = localStorage.getItem("curbside-theme");
    if (t === "light" || t === "dark") return t;
  } catch {
    /* storage unavailable */
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function initialView(): View {
  const h = window.location.hash.replace("#", "");
  return h === "quality" || h === "method" || h === "forecast" || h === "flows" || h === "policy" ? h : "pulse";
}

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [view, setView] = useState<View>(initialView);
  const [month, setMonth] = useState<string>("");
  const [md, setMd] = useState<MonthData | null>(null);
  const [day, setDay] = useState<string>("");
  const [hourIdx, setHourIdx] = useState(0);
  const [sel, setSel] = useState<SourceSel>("both");
  const [measure, setMeasure] = useState<Measure>("count");
  const [borough, setBorough] = useState("All");
  const [zone, setZone] = useState<number | null>(null);
  const [hover, setHover] = useState<{ id: number; x: number; y: number } | null>(null);
  const [playing, setPlaying] = useState(false);
  const [showTable, setShowTable] = useState(false);
  const [dockOpen, setDockOpen] = useState(() => window.innerWidth >= 768);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("curbside-theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  useEffect(() => {
    const onHash = () => setView(initialView());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    loadMeta()
      .then((m) => {
        setMeta(m);
        const ms = monthsAvailable(m);
        setMonth(ms[ms.length - 1]);
      })
      .catch((e) => setError(String(e.message || e)));
  }, []);

  // Load the month's binaries; keep the same day-of-month and hour where possible.
  useEffect(() => {
    if (!meta || !month) return;
    let alive = true;
    loadMonth(meta, month)
      .then((d) => {
        if (!alive) return;
        setMd(d);
        const infos = hourInfos(d.startUtc, d.hours);
        const days = [...groupByDay(infos).keys()];
        const prevDom = day ? day.slice(8) : "";
        const nd = days.find((x) => x.slice(8) === prevDom) ?? days[Math.min(days.length - 1, 13)];
        setDay(nd);
        const prevHour = md ? hourInfos(md.startUtc, md.hours)[hourIdx]?.hour ?? 18 : 18;
        const h = infos.find((x) => x.day === nd && x.hour === prevHour) ?? infos.find((x) => x.day === nd)!;
        setHourIdx(h.index);
      })
      .catch((e) => alive && setError(String(e.message || e)));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta, month]);

  const infos: HourInfo[] = useMemo(() => (md ? hourInfos(md.startUtc, md.hours) : []), [md]);
  const byDay = useMemo(() => groupByDay(infos), [infos]);
  const days = useMemo(() => [...byDay.keys()], [byDay]);
  const dayHours = byDay.get(day) ?? [];

  // Wait time exists only for Uber/Lyft, so that measure always shows that service.
  const effSel: SourceSel = measure === "wait" ? "hvfhv" : sel;
  const loadingMonth = !!month && md?.month !== month;

  const values = useMemo(
    () => (meta && md && infos[hourIdx] ? valuesAt(meta, md, effSel, measure, hourIdx) : null),
    [meta, md, effSel, measure, hourIdx, infos],
  );

  const stepHour = useCallback(
    (i: number) => {
      if (!infos.length) return;
      const clamped = Math.min(infos.length - 1, Math.max(0, i));
      setHourIdx(clamped);
      setDay(infos[clamped].day);
    },
    [infos],
  );

  const selectDay = useCallback(
    (d: string) => {
      const cur = infos[hourIdx]?.hour ?? 18;
      const hs = byDay.get(d) ?? [];
      const h = hs.find((x) => x.hour === cur) ?? hs[0];
      setDay(d);
      if (h) setHourIdx(h.index);
    },
    [infos, hourIdx, byDay],
  );

  // Playback: advance one hour per tick; stop at the end of the month.
  const tick = useRef<number | null>(null);
  useEffect(() => {
    if (!playing) return;
    tick.current = window.setInterval(() => {
      setHourIdx((i) => {
        if (i >= infos.length - 1) {
          setPlaying(false);
          return i;
        }
        setDay(infos[i + 1].day);
        return i + 1;
      });
    }, PLAY_MS);
    return () => {
      if (tick.current) window.clearInterval(tick.current);
    };
  }, [playing, infos]);

  if (error) {
    return (
      <div className="h-full grid place-items-center p-6">
        <div className="panel p-6 max-w-md">
          <h1 className="font-display text-xl">Data could not be loaded</h1>
          <p className="mt-2 text-sm text-[var(--ink-2)]">{error}</p>
          <p className="mt-2 text-sm text-[var(--ink-2)]">Build the artifacts with <code>uv run curbside build-web</code>.</p>
        </div>
      </div>
    );
  }
  if (!meta) return <div className="h-full grid place-items-center text-[var(--ink-2)]">Loading…</div>;

  const months = monthsAvailable(meta);
  const unit = measureUnit(meta, effSel, measure);
  const when = infos[hourIdx] ? `${dayLabel(infos[hourIdx].day)}, ${hourLabel(infos[hourIdx])}` : "";
  const sourceNote =
    effSel === "both" ? "sum of two different services" : effSel === "yellow" ? "yellow taxis only" : "Uber/Lyft only";
  const dstSplit = new Set(meta.dst_split_hours_utc);
  const hoverZone = hover ? meta.zones.find((z) => z.id === hover.id) : null;
  const hoverVal = hover && values ? values[meta.zones.findIndex((z) => z.id === hover.id)] : null;

  const nav = (
    <nav aria-label="Main" className="flex items-center gap-0.5 sm:gap-1 text-[12px] sm:text-[13px] overflow-x-auto max-w-[calc(100vw-96px)] [scrollbar-width:none]">
      {(
        [
          ["pulse", "City Pulse", "Pulse"],
          ["flows", "Flows", "Flows"],
          ["forecast", "Forecasts", "Forecast"],
          ["policy", "Congestion pricing", "Policy"],
          ["quality", "Data quality", "Quality"],
          ["method", "Method", "Method"],
        ] as [View, string, string][]
      ).map(([v, label, short]) => (
        <a
          key={v}
          href={`#${v}`}
          aria-current={view === v ? "page" : undefined}
          className={`px-2 sm:px-2.5 py-1.5 rounded-lg whitespace-nowrap ${view === v ? "bg-[var(--surface-2)] text-[var(--ink)] font-medium" : "text-[var(--ink-2)] hover:text-[var(--ink)]"}`}
        >
          <span className="hidden sm:inline">{label}</span>
          <span className="sm:hidden">{short}</span>
        </a>
      ))}
    </nav>
  );

  return (
    <div className="h-full flex flex-col">
      <header className="h-14 shrink-0 flex items-center gap-3 px-4 border-b border-[var(--border)] bg-[var(--surface)] z-20">
        <a href="#pulse" className="flex items-baseline gap-2 min-w-0">
          <span className="font-display text-[20px] font-semibold tracking-tight hidden sm:inline">Curbside</span>
          <span className="font-display text-[18px] font-semibold tracking-tight sm:hidden" aria-label="Curbside">CS</span>
          <span className="hidden md:inline text-[12px] text-[var(--muted)] truncate">NYC taxi &amp; ride-hail pickups, hour by hour</span>
        </a>
        <div className="ml-auto flex items-center gap-2">
          {nav}
          <button
            type="button"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            className="h-8 w-8 grid place-items-center rounded-lg text-[var(--ink-2)] hover:bg-[var(--surface-2)]"
          >
            {theme === "dark" ? (
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="3.2" fill="currentColor" /><g stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">{[0, 45, 90, 135, 180, 225, 270, 315].map((a) => <line key={a} x1="8" y1="1.2" x2="8" y2="2.8" transform={`rotate(${a} 8 8)`} />)}</g></svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M13.5 9.6A5.8 5.8 0 0 1 6.4 2.5a5.8 5.8 0 1 0 7.1 7.1z" fill="currentColor" /></svg>
            )}
          </button>
        </div>
      </header>

      {view === "flows" && <main className="flex-1 overflow-auto" tabIndex={0}><FlowsPage meta={meta} theme={theme} /></main>}
      {view === "policy" && <main className="flex-1 overflow-auto" tabIndex={0}><PolicyPage theme={theme} /></main>}
      {view === "forecast" && <main className="flex-1 overflow-auto" tabIndex={0}><ForecastPage meta={meta} theme={theme} /></main>}
      {view === "quality" && <main className="flex-1 overflow-auto" tabIndex={0}><QualityPage meta={meta} theme={theme} /></main>}
      {view === "method" && <main className="flex-1 overflow-auto" tabIndex={0}><MethodPage meta={meta} /></main>}

      {view === "pulse" && (
        <main className="relative flex-1 min-h-0">
          <h1 className="sr-only">City Pulse: hourly pickups by taxi zone</h1>
          <MapView
            meta={meta}
            values={values}
            measure={measure}
            theme={theme}
            borough={borough}
            selected={zone}
            onSelect={setZone}
            onHover={(id, pt) => setHover(id !== null && pt ? { id, ...pt } : null)}
          />

          {hover && hoverZone && (
            <div
              className="pointer-events-none absolute z-10 panel px-3 py-2 text-[12px] hidden md:block"
              style={{ left: Math.min(hover.x + 14, window.innerWidth - 240), top: hover.y + 14 }}
              role="tooltip"
            >
              <div className="font-medium text-[var(--ink)]">{hoverZone.zone}</div>
              <div className="text-[var(--muted)]">{hoverZone.borough}</div>
              <div className="mt-1 tabular text-[var(--ink)]">
                {hoverVal === null ? "—" : formatValue(hoverVal, measure)}
                <span className="text-[var(--muted)]"> {measure === "wait" ? "median wait" : measure === "density" ? "per km²" : "pickups"}</span>
              </div>
            </div>
          )}

          {/* Left dock: filters + legend */}
          <div className="absolute left-3 top-3 z-10 flex flex-col gap-3 w-[300px] max-w-[calc(100%-24px)] max-h-[calc(100%-230px)] overflow-auto">
            <button
              type="button"
              className="panel md:hidden self-start px-3 py-2 text-[13px] font-medium"
              aria-expanded={dockOpen}
              aria-controls="dock"
              onClick={() => setDockOpen(!dockOpen)}
            >
              {dockOpen ? "Hide filters" : "Filters & legend"}
            </button>
            <div id="dock" className={`${dockOpen ? "flex" : "hidden"} md:flex flex-col gap-3`}>
            <section className="panel p-3 space-y-3" aria-label="Filters">
              <div>
                <div className="text-[11px] text-[var(--muted)] mb-1">Service</div>
                <div className="seg" role="group" aria-label="Service">
                  {(
                    [
                      ["yellow", "Yellow taxi"],
                      ["hvfhv", "Uber/Lyft"],
                      ["both", "Both"],
                    ] as [SourceSel, string][]
                  ).map(([v, l]) => (
                    <button key={v} type="button" aria-pressed={effSel === v} disabled={measure === "wait" && v !== "hvfhv"} onClick={() => setSel(v)}>
                      {v !== "both" && <span className="inline-block h-2 w-2 rounded-full mr-1.5 align-middle" style={{ background: `var(--series-${v})` }} />}
                      {l}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <div className="text-[11px] text-[var(--muted)] mb-1">Measure</div>
                <div className="seg" role="group" aria-label="Measure">
                  {(
                    [
                      ["count", "Pickups"],
                      ["density", "Per km²"],
                      ["wait", "Wait time"],
                    ] as [Measure, string][]
                  ).map(([v, l]) => (
                    <button key={v} type="button" aria-pressed={measure === v} onClick={() => setMeasure(v)}>
                      {l}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex items-end gap-2">
                <div className="flex-1">
                  <label htmlFor="borough" className="text-[11px] text-[var(--muted)] block mb-1">Borough</label>
                  <select id="borough" value={borough} onChange={(e) => setBorough(e.target.value)} className="h-8 w-full rounded-lg border border-[var(--border)] bg-[var(--surface-2)] px-2 text-[13px]">
                    {BOROUGHS.map((b) => <option key={b} value={b}>{b === "All" ? "All boroughs" : b === "EWR" ? "Newark Airport (EWR)" : b}</option>)}
                  </select>
                </div>
                <button type="button" aria-pressed={showTable} onClick={() => setShowTable(!showTable)} className="h-8 px-3 rounded-lg border border-[var(--border)] text-[13px] hover:bg-[var(--surface-2)] aria-pressed:bg-[var(--surface-2)]">
                  {showTable ? "Hide table" : "Table"}
                </button>
              </div>
            </section>
            {showTable ? (
              <TableView meta={meta} values={values} measure={measure} unit={unit} when={when} borough={borough} onSelect={setZone} />
            ) : (
              <Legend measure={measure} theme={theme} unit={unit} when={when} sourceNote={sourceNote} />
            )}
            </div>
          </div>

          {/* Right inspector */}
          {md && (
            <div className="absolute right-3 top-3 z-10 w-[340px] max-w-[calc(100%-24px)] hidden lg:block">
              <Inspector meta={meta} md={md} infos={infos} hourIdx={hourIdx} day={day} zoneId={zone} sel={effSel} measure={measure} values={values} onClose={() => setZone(null)} />
            </div>
          )}

          {/* Mobile: compact card for the tapped zone */}
          {zone !== null && (
            <div className="lg:hidden absolute left-3 right-3 bottom-[200px] z-10 panel px-3 py-2 flex items-center gap-3">
              <div className="min-w-0">
                <div className="text-[13px] font-medium truncate">{meta.zones.find((z) => z.id === zone)?.zone}</div>
                <div className="text-[11px] text-[var(--muted)]">{meta.zones.find((z) => z.id === zone)?.borough}</div>
              </div>
              <div className="ml-auto font-display text-[20px] tabular">
                {values ? formatValue(values[meta.zones.findIndex((z) => z.id === zone)], measure) : "—"}
              </div>
              <button type="button" onClick={() => setZone(null)} aria-label="Clear selected zone" className="text-[var(--muted)] px-1">✕</button>
            </div>
          )}

          {/* Bottom time rail */}
          {md && day && (
            <div className="absolute bottom-6 left-1/2 -translate-x-1/2 z-10 w-[min(760px,calc(100%-24px))] lg:left-[calc(50%-20px)]">
              {loadingMonth && <div className="mb-2 text-center text-[12px] text-[var(--muted)]">Loading month…</div>}
              <TimeControls
                months={months}
                month={month}
                onMonth={(m) => {
                  setPlaying(false);
                  setMonth(m);
                }}
                days={days}
                day={day}
                onDay={selectDay}
                dayHours={dayHours}
                hourIdx={hourIdx}
                onHour={stepHour}
                playing={playing}
                onPlay={setPlaying}
                dstSplit={dstSplit}
              />
            </div>
          )}
        </main>
      )}
    </div>
  );
}
