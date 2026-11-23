import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// WCAG 2.1 A/AA checks (including colour contrast) on every page, both themes, desktop.
const pages = ["pulse", "flows", "forecast", "policy", "quality", "method"];

for (const theme of ["light", "dark"] as const) {
  for (const p of pages) {
    test(`a11y: ${p} (${theme})`, async ({ page }, info) => {
      test.skip(info.project.name !== "desktop", "run once");
      await page.addInitScript((t) => localStorage.setItem("curbside-theme", t), theme);
      await page.goto(`/#${p}`);
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(800);
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .exclude(".maplibregl-canvas") // WebGL canvas: covered by the table alternative
        .analyze();
      const summary = results.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.length} × ${v.nodes[0]?.target}`);
      expect(summary, summary.join("\n")).toEqual([]);
    });
  }
}
