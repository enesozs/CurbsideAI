import { expect, test } from "@playwright/test";

// Performance budget (first map paint under 2 s on desktop; scrubbing stays smooth).
// Measured against the local server, so this guards against regressions rather than modelling networks.
test("map is ready within budget and hour steps stay fast", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "budget defined for desktop");
  const t0 = Date.now();
  await page.goto("/#pulse");
  await expect(page.locator("canvas.maplibregl-canvas")).toBeVisible();
  await page.waitForFunction(() => !document.body.innerText.includes("Loading map…"), null, { timeout: 10_000 });
  const readyMs = Date.now() - t0;
  console.log(`map ready in ${readyMs} ms`);
  expect(readyMs).toBeLessThan(2000);

  const hour = page.locator('[aria-live="polite"]');
  const steps = 24;
  const start = Date.now();
  for (let i = 0; i < steps; i++) {
    const before = await hour.innerText();
    await page.locator("body").press(i % 2 ? "ArrowLeft" : "ArrowRight");
    await expect(hour).not.toHaveText(before);
  }
  const perStep = (Date.now() - start) / steps;
  console.log(`hour step: ${perStep.toFixed(1)} ms`);
  expect(perStep).toBeLessThan(100);
});
