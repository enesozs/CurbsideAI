import { useMemo, useState } from "react";
import type { Meta } from "../lib/data";
import type { Measure } from "../lib/scales";
import { formatValue, MISSING } from "../lib/values";

interface Props {
  meta: Meta;
  values: Float64Array | null;
  measure: Measure;
  unit: string;
  when: string;
  borough: string;
  onSelect: (id: number) => void;
}

/** Accessible alternative to the map: every zone's value for the current hour, sortable. */
export function TableView({ meta, values, measure, unit, when, borough, onSelect }: Props) {
  const [sortBy, setSortBy] = useState<"value" | "zone">("value");
  const rows = useMemo(() => {
    const r = meta.zones
      .map((z, i) => ({ z, v: values ? values[i] : MISSING }))
      .filter((r) => borough === "All" || r.z.borough === borough);
    r.sort((a, b) => (sortBy === "value" ? b.v - a.v : a.z.zone.localeCompare(b.z.zone)));
    return r;
  }, [meta, values, borough, sortBy]);

  return (
    <div className="panel p-0 overflow-hidden w-full max-h-[60vh] flex flex-col">
      <div className="px-4 pt-3 pb-2 border-b border-[var(--hairline)]">
        <div className="text-[13px] font-medium">{unit}</div>
        <div className="text-[11px] text-[var(--muted)]">{when} · {borough === "All" ? "all boroughs" : borough}</div>
      </div>
      <div className="overflow-auto" tabIndex={0} aria-label="Zone values table">
        <table className="w-full text-[12px] tabular">
          <caption className="sr-only">{unit}, {when}</caption>
          <thead className="sticky top-0 bg-[var(--surface)] text-[var(--muted)]">
            <tr>
              <th scope="col" className="text-left font-normal px-4 py-1.5">
                <button type="button" onClick={() => setSortBy("zone")} aria-pressed={sortBy === "zone"}>Zone</button>
              </th>
              <th scope="col" className="text-left font-normal px-2 py-1.5">Borough</th>
              <th scope="col" className="text-right font-normal px-4 py-1.5">
                <button type="button" onClick={() => setSortBy("value")} aria-pressed={sortBy === "value"}>Value</button>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ z, v }) => (
              <tr key={z.id} className="border-t border-[var(--hairline)] hover:bg-[var(--surface-2)]">
                <td className="px-4 py-1">
                  <button type="button" className="text-left hover:underline" onClick={() => onSelect(z.id)}>{z.zone}</button>
                </td>
                <td className="px-2 py-1 text-[var(--ink-2)]">{z.borough}</td>
                <td className="px-4 py-1 text-right">{formatValue(v, measure)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
