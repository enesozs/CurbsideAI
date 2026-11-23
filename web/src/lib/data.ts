// Loaders for the static artifacts written by `curbside build-web` (see src/curbside/artifacts.py).

export type Source = "yellow" | "hvfhv";
export const SOURCES: Source[] = ["yellow", "hvfhv"];

export interface Zone {
  id: number;
  zone: string;
  borough: string;
  area_km2: number;
  label: [number, number];
}

export interface MonthIndex {
  start_utc: number;
  hours: number;
  total: number;
  max: number;
  p99: number;
}

export interface SourceMeta {
  label: string;
  unit: string;
  dtype: "uint16" | "uint32";
  peak_zone_hour: number;
  months: Record<string, MonthIndex>;
  daily: { file: string; start_date: string; days: number };
}

export interface Meta {
  generated_at: string;
  window: [string, string];
  timezone: string;
  zones: Zone[];
  sources: Record<Source, SourceMeta>;
  wait: { min_n: number; suppressed: number; months: Record<string, { file: string }> };
  dst_split_hours_utc: number[];
  attribution: { text: string; url: string | null }[];
}

export interface MonthData {
  month: string;
  startUtc: number;
  hours: number;
  counts: Partial<Record<Source, Uint16Array | Uint32Array>>;
  wait: Uint16Array | null;
}

const cache = new Map<string, Promise<ArrayBuffer>>();

async function fetchBuffer(path: string): Promise<ArrayBuffer> {
  if (!cache.has(path)) {
    cache.set(
      path,
      fetch(`/data/${path}`).then((r) => {
        if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
        return r.arrayBuffer();
      }),
    );
  }
  try {
    return await cache.get(path)!;
  } catch (e) {
    cache.delete(path);
    throw e;
  }
}

export async function loadMeta(): Promise<Meta> {
  const r = await fetch("/data/meta.json");
  if (!r.ok) throw new Error(`meta.json: HTTP ${r.status}`);
  return r.json();
}

export async function loadMonth(meta: Meta, month: string): Promise<MonthData> {
  const counts: MonthData["counts"] = {};
  let startUtc = 0;
  let hours = 0;
  await Promise.all(
    SOURCES.map(async (s) => {
      const idx = meta.sources[s].months[month];
      if (!idx) return;
      const buf = await fetchBuffer(`zh/${s}/${month}.bin`);
      counts[s] = meta.sources[s].dtype === "uint16" ? new Uint16Array(buf) : new Uint32Array(buf);
      startUtc = idx.start_utc;
      hours = idx.hours;
    }),
  );
  const waitMeta = meta.wait.months[month];
  const wait = waitMeta ? new Uint16Array(await fetchBuffer(waitMeta.file)) : null;
  return { month, startUtc, hours, counts, wait };
}

export async function loadDaily(meta: Meta, source: Source): Promise<Uint32Array> {
  return new Uint32Array(await fetchBuffer(meta.sources[source].daily.file));
}

export function monthsAvailable(meta: Meta): string[] {
  const set = new Set<string>();
  for (const s of SOURCES) Object.keys(meta.sources[s].months).forEach((m) => set.add(m));
  return [...set].sort();
}
