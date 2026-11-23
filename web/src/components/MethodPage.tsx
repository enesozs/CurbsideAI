import type { Meta } from "../lib/data";

export function MethodPage({ meta }: { meta: Meta }) {
  return (
    <article className="mx-auto max-w-3xl px-4 sm:px-6 py-8 text-[15px] leading-relaxed text-[var(--ink-2)]">
      <h1 className="font-display text-[32px] leading-tight text-[var(--ink)]">How City Pulse is built</h1>
      <p className="mt-3">
        Each colour on the map is the number of <strong className="text-[var(--ink)]">completed pickups</strong> in one NYC taxi zone during one hour, counted from the
        official TLC trip records. Yellow taxis and high-volume for-hire trips (Uber, Lyft) are separate populations and are always labelled; “Both” is their plain sum.
      </p>
      <h2 className="font-display text-[22px] text-[var(--ink)] mt-8">What the numbers are — and aren't</h2>
      <ul className="mt-2 list-disc pl-5 space-y-1.5">
        <li><strong className="text-[var(--ink)]">Not traffic or congestion.</strong> Pickups measure how much a service was used, not road speed or vehicle volume.</li>
        <li><strong className="text-[var(--ink)]">Not all travel.</strong> No subway, bus, private car, bike or walking data.</li>
        <li><strong className="text-[var(--ink)]">Not real-time.</strong> TLC publishes monthly with about a two-month delay. Coverage: {meta.window[0]} to {meta.window[1]}.</li>
        <li><strong className="text-[var(--ink)]">Zones, not GPS.</strong> TLC reports pickup locations as taxi zones; nothing finer is shown.</li>
        <li><strong className="text-[var(--ink)]">Wait time</strong> is the median minutes from an Uber/Lyft request to pickup, shown only when at least {meta.wait.min_n} trips back it. It indicates service tension, not congestion.</li>
      </ul>
      <h2 className="font-display text-[22px] text-[var(--ink)] mt-8">Counting rules</h2>
      <ul className="mt-2 list-disc pl-5 space-y-1.5">
        <li>Trips count in the hour of their pickup, in New York time. Times in the source files are local wall-clock times.</li>
        <li>On the night clocks fall back, two real hours share the label “1 AM”; their trips are split evenly between them and flagged. The skipped spring hour never appears.</li>
        <li>Yellow-taxi records with a negative fare total are reversal entries and are not counted. This rule reproduces TLC's own monthly totals best (see Data quality).</li>
        <li>Zones 264 and 265 (unknown / outside NYC) are not mapped. Every zone-hour in a published month has a value; zero means no recorded pickups.</li>
        <li>Colour classes are fixed and log-spaced, so the same colour means the same range at every hour.</li>
      </ul>
      <h2 className="font-display text-[22px] text-[var(--ink)] mt-8">Sources and attribution</h2>
      <ul className="mt-2 space-y-1.5">
        {meta.attribution.map((a) => (
          <li key={a.text}>{a.url ? <a className="underline hover:text-[var(--ink)]" href={a.url}>{a.text}</a> : a.text}</li>
        ))}
      </ul>
      <p className="mt-6 text-[13px] text-[var(--muted)]">Curbside is an independent analytical and learning project. Code and full methodology: docs/ in the repository.</p>
    </article>
  );
}
