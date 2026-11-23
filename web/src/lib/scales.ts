// Fixed classed scales (log-spaced breaks) so colours mean the same thing at every hour/month.
// Ramps: dataviz reference blue (counts) and an OKLCH-generated orange (wait), light & dark steps.

export type Measure = "count" | "density" | "wait";
export type Theme = "light" | "dark";

export interface Scale {
  breaks: number[]; // class i covers [breaks[i-1], breaks[i])
  colors: Record<Theme, string[]>; // breaks.length + 1 classes, class 0 = below first break
  zero: Record<Theme, string>;
  format: (v: number) => string;
}

const BLUE_LIGHT = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
const BLUE_DARK = ["#123a6e", "#184f95", "#1c5cab", "#2a78d6", "#5598e7", "#86b6ef", "#cde2fb"];
const ORANGE_LIGHT = ["#fee5d9", "#ffc6a9", "#fda578", "#f2874c", "#de6e29", "#bf5914", "#9c470d", "#7b3606"];
const ORANGE_DARK = ["#5a2804", "#7b3606", "#9c470d", "#bf5914", "#de6e29", "#f2874c", "#fda578", "#ffc6a9"];
const ZERO = { light: "#e7e6e1", dark: "#2c2c2a" };

const countFmt = (v: number) => (v >= 1000 ? `${v / 1000}k` : `${v}`);

export const SCALES: Record<Measure, Scale> = {
  count: {
    breaks: [3, 10, 30, 100, 300, 1000],
    colors: { light: BLUE_LIGHT, dark: BLUE_DARK },
    zero: ZERO,
    format: countFmt,
  },
  density: {
    breaks: [3, 10, 30, 100, 300, 1000],
    colors: { light: BLUE_LIGHT, dark: BLUE_DARK },
    zero: ZERO,
    format: countFmt,
  },
  wait: {
    breaks: [2, 3, 4, 5, 6, 8, 10],
    colors: { light: ORANGE_LIGHT, dark: ORANGE_DARK },
    zero: ZERO,
    format: (v) => `${v}`,
  },
};

// MapLibre paint expression: feature-state "v" → colour. v < 0 = missing (drawn by the hatch layer),
// v == 0 = no trips (neutral), otherwise classed by breaks. Wait has no zero class.
export function fillColorExpr(measure: Measure, theme: Theme): unknown {
  const s = SCALES[measure];
  const cols = s.colors[theme];
  const step: unknown[] = ["step", ["coalesce", ["feature-state", "v"], -1], cols[0]];
  s.breaks.forEach((b, i) => step.push(b, cols[i + 1]));
  if (measure === "wait") return step;
  return ["case", ["==", ["coalesce", ["feature-state", "v"], -1], 0], s.zero[theme], step];
}

export function classOf(measure: Measure, v: number): number {
  const b = SCALES[measure].breaks;
  let i = 0;
  while (i < b.length && v >= b[i]) i++;
  return i;
}
