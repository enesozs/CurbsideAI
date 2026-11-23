import { SCALES, type Measure, type Theme } from "../lib/scales";

interface Props {
  measure: Measure;
  theme: Theme;
  unit: string;
  when: string;
  sourceNote: string;
}

export function Legend({ measure, theme, unit, when, sourceNote }: Props) {
  const s = SCALES[measure];
  const cols = s.colors[theme];
  const labels =
    measure === "wait"
      ? [`<${s.breaks[0]}`, ...s.breaks.map((b, i) => (i === s.breaks.length - 1 ? `${b}+` : `${b}`))]
      : [`<${s.format(s.breaks[0])}`, ...s.breaks.map((b, i) => (i === s.breaks.length - 1 ? `${s.format(b)}+` : s.format(b)))];
  return (
    <figure className="panel p-3 w-[300px] max-w-full" aria-label="Map legend">
      <figcaption className="text-[12px] leading-snug text-[var(--ink)] font-medium">{unit}</figcaption>
      <div className="mt-0.5 text-[11px] text-[var(--muted)] tabular">
        Observed · per taxi zone · {when}
      </div>
      <div className="mt-2.5 flex items-stretch gap-[2px]" role="list">
        {measure !== "wait" && (
          <div role="listitem" className="flex flex-col items-center mr-1.5">
            <div className="h-3 w-5 rounded-[3px]" style={{ background: s.zero[theme] }} />
            <span className="mt-1 text-[10px] text-[var(--muted)]">0</span>
          </div>
        )}
        {cols.map((c, i) => (
          <div role="listitem" key={c} className="flex-1 flex flex-col items-start">
            <div className="h-3 w-full first:rounded-l-[3px]" style={{ background: c, borderRadius: i === 0 ? "3px 0 0 3px" : i === cols.length - 1 ? "0 3px 3px 0" : 0 }} />
            <span className="mt-1 text-[10px] text-[var(--muted)] tabular whitespace-nowrap">{labels[i]}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-2 text-[11px] text-[var(--muted)]">
        <span
          className="inline-block h-3 w-5 rounded-[3px] border border-[var(--border)]"
          style={{
            backgroundImage:
              "repeating-linear-gradient(135deg, transparent 0 3px, var(--muted) 3px 4px)",
          }}
        />
        {measure === "wait" ? "Not shown: fewer than 10 trips that hour" : "No data for this source/month"}
      </div>
      <div className="mt-1 text-[11px] text-[var(--muted)]">
        Log-spaced classes · {sourceNote}
      </div>
    </figure>
  );
}
