import { useMemo } from "react";
import type { Meta, MonthData, Source } from "../lib/data";
import type { HourInfo } from "../lib/time";
import { countAt, formatValue, MISSING, sourcesOf, type SourceSel } from "../lib/values";
import type { Measure } from "../lib/scales";
import { ZoneChart, type SeriesPoint } from "./ZoneChart";

interface Props {
  meta: Meta;
  md: MonthData;
  infos: HourInfo[];
  hourIdx: number;
  day: string;
  zoneId: number | null;
  sel: SourceSel;
  measure: Measure;
  values: Float64Array | null;
  onClose: () => void;
}

export function Inspector({ meta, md, infos, hourIdx, day, zoneId, sel, measure, values, onClose }: Props) {
  const nZ = meta.zones.length;
  const zi = zoneId === null ? -1 : meta.zones.findIndex((z) => z.id === zoneId);
  const zone = zi >= 0 ? meta.zones[zi] : null;

  // Week window around the selected day, clipped to the loaded month.
  const series = useMemo(() => {
    const dayStart = infos.findIndex((h) => h.day === day);
    const from = Math.max(0, dayStart - 72);
    const to = Math.min(infos.length, dayStart + 96);
    const pts: SeriesPoint[] = [];
    const srcs: Source[] = sourcesOf(sel).filter((s) => md.counts[s]);
    for (const s of srcs) {
      for (let i = from; i < to; i++) {
        let v = 0;
        if (zi >= 0) v = countAt(md, s, i, zi, nZ);
        else for (let k = 0; k < nZ; k++) v += countAt(md, s, i, k, nZ);
        if (v !== MISSING) pts.push({ t: new Date(infos[i].utc * 1000), v, source: s });
      }
    }
    return pts;
  }, [infos, day, sel, md, zi, nZ]);

  const rank = useMemo(() => {
    if (!values || zi < 0 || values[zi] === MISSING) return null;
    const v = values[zi];
    let higher = 0;
    let valid = 0;
    for (const x of values) {
      if (x === MISSING) continue;
      valid++;
      if (x > v) higher++;
    }
    return { rank: higher + 1, of: valid };
  }, [values, zi]);

  const current = infos[hourIdx] ? new Date(infos[hourIdx].utc * 1000) : null;
  const v = values && zi >= 0 ? values[zi] : null;
  const city = useMemo(() => {
    let t = 0;
    for (const s of sourcesOf(sel)) {
      if (!md.counts[s]) return null;
      for (let k = 0; k < nZ; k++) t += md.counts[s]![hourIdx * nZ + k];
    }
    return t;
  }, [md, sel, hourIdx, nZ]);

  return (
    <aside className="panel p-4 w-full" aria-label={zone ? `Details for ${zone.zone}` : "Citywide details"}>
      <div className="flex items-start gap-2">
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-wide text-[var(--muted)]">{zone ? zone.borough : "All five boroughs"}</div>
          <h2 className="font-display text-[20px] leading-tight mt-0.5">{zone ? zone.zone : "Citywide"}</h2>
        </div>
        {zone && (
          <button type="button" onClick={onClose} className="ml-auto text-[12px] text-[var(--ink-2)] hover:text-[var(--ink)] px-2 py-1 rounded-md hover:bg-[var(--surface-2)]">
            Clear
          </button>
        )}
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-3">
        <div>
          <dt className="text-[11px] text-[var(--muted)]">{zone ? "This hour" : "Pickups this hour"}</dt>
          <dd className="font-display text-[26px] leading-none mt-1 tabular">
            {zone ? (v === null ? "—" : formatValue(v, measure)) : city === null ? "—" : city.toLocaleString("en-US")}
          </dd>
        </div>
        {zone && (
          <div>
            <dt className="text-[11px] text-[var(--muted)]">Rank among zones</dt>
            <dd className="font-display text-[26px] leading-none mt-1 tabular">
              {rank ? (
                <>
                  {rank.rank}
                  <span className="text-[13px] text-[var(--muted)] font-sans"> / {rank.of}</span>
                </>
              ) : (
                "—"
              )}
            </dd>
          </div>
        )}
        {zone && (
          <div className="col-span-2 text-[11px] text-[var(--muted)] tabular">
            Zone {zone.id} · {zone.area_km2.toFixed(2)} km²
          </div>
        )}
      </dl>

      <div className="mt-4">
        <div className="text-[12px] font-medium text-[var(--ink)]">Pickups per hour, week around this day</div>
        <div className="text-[11px] text-[var(--muted)] mb-1">Observed · New York time · {zone ? "this zone" : "all zones"}</div>
        {series.length ? (
          <ZoneChart
            data={series}
            current={current}
            yLabel="pickups"
            ariaLabel={`Hourly pickups for ${zone ? zone.zone : "all of New York City"} around ${day}`}
          />
        ) : (
          <div className="text-[12px] text-[var(--muted)] py-6 text-center">No data for this selection.</div>
        )}
      </div>
    </aside>
  );
}
