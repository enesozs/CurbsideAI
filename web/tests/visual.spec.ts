import { expect, test } from "@playwright/test";

// Screenshot regression for key views (desktop, light theme). Baselines are macOS/Chrome renders,
// so this suite runs locally (skipped in CI). Update intentionally with: npx playwright test visual -u
const views: { name: string; hash: string; ready: (p: import("@playwright/test").Page) => Promise<void> }[] = [
  { name: "pulse", hash: "pulse", ready: async (p) => { await p.waitForFunction(() => !document.body.innerText.includes("Loading map…")); } },
  { name: "forecast", hash: "forecast", ready: async (p) => { await p.getByRole("img", { name: /day-ahead forecasts/ }).waitFor(); } },
  { name: "quality", hash: "quality", ready: async (p) => { await p.getByRole("heading", { name: "Data quality" }).waitFor(); } },
  { name: "policy", hash: "policy", ready: async (p) => { await p.getByRole("img", { name: /confidence intervals/ }).waitFor(); } },
];

for (const v of views) {
  test(`visual: ${v.name}`, async ({ page }, info) => {
    test.skip(info.project.name !== "desktop" || !!process.env.CI, "local desktop baselines only");
    await page.addInitScript(() => localStorage.setItem("curbside-theme", "light"));
    await page.goto(`/#${v.hash}`);
    await v.ready(page);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(1200);
    await expect(page).toHaveScreenshot(`${v.name}.png`, {
      maxDiffPixelRatio: 0.02,
      // The map attribution stays visible: screenshots of the map are OpenStreetMap-derived works
      // and must carry the credit (ODbL).
      animations: "disabled",
    });
  });
}
