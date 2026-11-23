// New York local-time helpers. All data hours are contiguous UTC hours; labels come from Intl,
// so the DST fall-back night shows both "1 AM EDT" and "1 AM EST" and spring-forward skips 2 AM.

export const TZ = "America/New_York";

const partsFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: TZ,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "numeric",
  hourCycle: "h23",
  timeZoneName: "short",
});

export interface HourInfo {
  index: number; // hour index within the month buffer
  utc: number; // epoch seconds
  day: string; // local date YYYY-MM-DD
  hour: number; // local hour 0-23
  tzName: string; // EST / EDT
}

export function hourInfos(startUtc: number, hours: number): HourInfo[] {
  const out: HourInfo[] = [];
  for (let i = 0; i < hours; i++) {
    const utc = startUtc + i * 3600;
    const p = Object.fromEntries(partsFmt.formatToParts(new Date(utc * 1000)).map((x) => [x.type, x.value]));
    out.push({
      index: i,
      utc,
      day: `${p.year}-${p.month}-${p.day}`,
      hour: Number(p.hour),
      tzName: p.timeZoneName,
    });
  }
  return out;
}

export function groupByDay(infos: HourInfo[]): Map<string, HourInfo[]> {
  const m = new Map<string, HourInfo[]>();
  for (const h of infos) {
    if (!m.has(h.day)) m.set(h.day, []);
    m.get(h.day)!.push(h);
  }
  return m;
}

export function hourLabel(h: HourInfo): string {
  const hr12 = h.hour % 12 === 0 ? 12 : h.hour % 12;
  return `${hr12}:00 ${h.hour < 12 ? "AM" : "PM"} ${h.tzName}`;
}

const dayFmt = new Intl.DateTimeFormat("en-US", {
  weekday: "short",
  month: "short",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

export function dayLabel(day: string): string {
  return dayFmt.format(new Date(`${day}T12:00:00Z`));
}

const monthFmt = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });

export function monthLabel(month: string): string {
  return monthFmt.format(new Date(`${month}-15T12:00:00Z`));
}
