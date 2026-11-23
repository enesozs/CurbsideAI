/** Make an Observable Plot SVG one accessible image: label the root, silence internal mark labels. */
export function a11yPlot<T extends Element>(plot: T, label: string): T {
  plot.setAttribute("role", "img");
  plot.setAttribute("aria-label", label);
  plot.querySelectorAll("[aria-label]").forEach((el) => {
    if (el !== plot) el.removeAttribute("aria-label");
  });
  plot.querySelectorAll("[aria-description]").forEach((el) => el.removeAttribute("aria-description"));
  return plot;
}
