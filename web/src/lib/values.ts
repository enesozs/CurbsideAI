import type { Meta, MonthData, Source } from "./data";
import type { Measure } from "./scales";

export type SourceSel = Source | "both";

export const MISSING = -1;

export function sourcesOf(sel: SourceSel): Source[] {
  return sel === "both" ? ["yellow", "hvfhv"] : [sel];
}

/** Count of pickups for zone index zi at hour i, or MISSING if a required source has no data. */
export function countAt(md: MonthData, sel: SourceSel, i: number, zi: number, nZ: number): number {
  let total = 0;
  for (const s of sourcesOf(sel)) {
    const arr = md.counts[s];
    if (!arr) return MISSING;
    total += arr[i * nZ + zi];
  }
  return total;
}

/** Values for every zone at hour i, in meta.zones order. */
export function valuesAt(meta: Meta, md: MonthData, sel: SourceSel, measure: Measure, i: number): Float64Array {
  const nZ = meta.zones.length;
  const out = new Float64Array(nZ);
  for (let zi = 0; zi < nZ; zi++) {
    if (measure === "wait") {
      const w = md.wait ? md.wait[i * nZ + zi] : meta.wait.suppressed;
      out[zi] = w === meta.wait.suppressed ? MISSING : w / 60;
    } else {
      const c = countAt(md, sel, i, zi, nZ);
      out[zi] = c === MISSING ? MISSING : measure === "density" ? c / meta.zones[zi].area_km2 : c;
    }
  }
  return out;
}

export function citywideAt(meta: Meta, md: MonthData, sel: SourceSel, i: number): number {
  const nZ = meta.zones.length;
  let t = 0;
  for (let zi = 0; zi < nZ; zi++) {
    const c = countAt(md, sel, i, zi, nZ);
    if (c === MISSING) return MISSING;
    t += c;
  }
  return t;
}

export function measureUnit(meta: Meta, sel: SourceSel, measure: Measure): string {
  if (measure === "wait") return "median minutes from request to pickup (Uber/Lyft)";
  const who =
    sel === "both"
      ? "completed yellow-taxi + Uber/Lyft pickups"
      : sel === "yellow"
        ? "completed yellow-taxi pickups"
        : "completed Uber/Lyft pickups";
  void meta;
  return measure === "density" ? `${who} / km² / hour` : `${who} / hour`;
}

export function formatValue(v: number, measure: Measure): string {
  if (v === MISSING) return "—";
  if (measure === "wait") return `${v.toFixed(1)} min`;
  if (measure === "density") return v < 10 ? v.toFixed(1) : Math.round(v).toLocaleString("en-US");
  return Math.round(v).toLocaleString("en-US");
}
