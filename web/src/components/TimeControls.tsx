import { useEffect } from "react";
import { dayLabel, hourLabel, monthLabel, type HourInfo } from "../lib/time";

interface Props {
  months: string[];
  month: string;
  onMonth: (m: string) => void;
  days: string[];
  day: string;
  onDay: (d: string) => void;
  dayHours: HourInfo[];
  hourIdx: number; // index into the month buffer
  onHour: (i: number) => void;
  playing: boolean;
  onPlay: (p: boolean) => void;
  dstSplit: Set<number>;
}

export function TimeControls(p: Props) {
  const pos = Math.max(0, p.dayHours.findIndex((h) => h.index === p.hourIdx));
  const current = p.dayHours[pos];
  const dayPos = p.days.indexOf(p.day);

  // Keyboard: ←/→ hour, Shift+←/→ day, Space play/pause (ignored while typing in a field).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA") && t.getAttribute("type") !== "range") return;
      if (e.key === " " && t?.tagName !== "BUTTON") {
        e.preventDefault();
        p.onPlay(!p.playing);
      } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        const dir = e.key === "ArrowRight" ? 1 : -1;
        if (t?.getAttribute("type") === "range") return; // native range handles hour steps
        e.preventDefault();
        if (e.shiftKey) {
          const nd = p.days[dayPos + dir];
          if (nd) p.onDay(nd);
        } else {
          p.onHour(p.hourIdx + dir);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [p, dayPos]);

  return (
    <div className="panel px-4 py-3 w-full" role="group" aria-label="Time controls">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <button
          type="button"
          onClick={() => p.onPlay(!p.playing)}
          aria-label={p.playing ? "Pause playback" : "Play through the hours"}
          className="h-9 w-9 shrink-0 rounded-full grid place-items-center bg-[var(--accent)] text-[var(--accent-ink)] hover:opacity-90"
        >
          {p.playing ? (
            <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><rect x="2" y="1" width="3.5" height="12" rx="1" fill="currentColor" /><rect x="8.5" y="1" width="3.5" height="12" rx="1" fill="currentColor" /></svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 1.5v11l9.5-5.5z" fill="currentColor" /></svg>
          )}
        </button>
        <div className="min-w-0">
          <div className="font-display text-[22px] leading-none tabular" aria-live="polite">
            {current ? hourLabel(current) : "—"}
          </div>
          <div className="text-[12px] text-[var(--muted)] mt-1">
            {dayLabel(p.day)} · New York time
            {current && p.dstSplit.has(current.utc) && (
              <span className="ml-1 text-[var(--ink-2)]" title="Clocks fall back: TLC timestamps cannot tell the two 1 AM hours apart, so trips are split evenly between them.">
                · repeated hour, trips split evenly
              </span>
            )}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <label className="sr-only" htmlFor="month">Month</label>
          <select
            id="month"
            value={p.month}
            onChange={(e) => p.onMonth(e.target.value)}
            className="h-8 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] px-2 text-[13px]"
          >
            {p.months.map((m) => (
              <option key={m} value={m}>{monthLabel(m)}</option>
            ))}
          </select>
          <label className="sr-only" htmlFor="day">Day</label>
          <select
            id="day"
            value={p.day}
            onChange={(e) => p.onDay(e.target.value)}
            className="h-8 rounded-lg border border-[var(--border)] bg-[var(--surface-2)] px-2 text-[13px] tabular"
          >
            {p.days.map((d) => (
              <option key={d} value={d}>{dayLabel(d)}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="mt-2">
        <label htmlFor="hour" className="sr-only">Hour of day</label>
        <input
          id="hour"
          className="rail"
          type="range"
          min={0}
          max={Math.max(0, p.dayHours.length - 1)}
          step={1}
          value={pos}
          aria-valuetext={current ? `${hourLabel(current)}, ${dayLabel(p.day)}` : undefined}
          onChange={(e) => {
            p.onPlay(false);
            p.onHour(p.dayHours[Number(e.target.value)].index);
          }}
        />
        <div className="flex justify-between text-[10px] text-[var(--muted)] tabular -mt-1 select-none" aria-hidden="true">
          {p.dayHours.map((h, i) =>
            i % 3 === 0 ? <span key={h.utc}>{h.hour === 0 ? "12a" : h.hour === 12 ? "12p" : h.hour % 12}</span> : null,
          )}
        </div>
      </div>
      <p className="sr-only">Keyboard: left and right arrows step one hour, Shift with arrows steps one day, Space plays or pauses.</p>
    </div>
  );
}
