import { expect, test } from "@playwright/test";

const shots = process.env.CS_SHOTS; // directory for screenshots, optional

test("City Pulse loads real data, map renders, controls work", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/#pulse");

  await expect(page.getByRole("group", { name: "Time controls" })).toBeVisible();
  await expect(page.locator("canvas.maplibregl-canvas")).toBeVisible();
  await page.waitForFunction(() => !document.body.innerText.includes("Loading map…"), null, { timeout: 30_000 });

  // On phones the filter dock starts collapsed.
  const dockToggle = page.getByRole("button", { name: "Filters & legend" });
  if (await dockToggle.isVisible()) await dockToggle.click();

  // Legend names the measure honestly (never "traffic").
  await expect(page.getByLabel("Map legend")).toContainText("pickups / hour");
  await expect(page.locator("body")).not.toContainText(/traffic/i);

  // Keyboard stepping changes the displayed hour.
  const hourText = page.locator('[aria-live="polite"]');
  const before = await hourText.innerText();
  await page.locator("body").press("ArrowRight");
  await expect(hourText).not.toHaveText(before);

  // Measure + service toggles.
  await page.getByRole("button", { name: "Wait time" }).click();
  await expect(page.getByLabel("Map legend")).toContainText("median minutes from request to pickup");
  await page.getByRole("button", { name: "Pickups", exact: true }).click();
  await page.getByRole("button", { name: /Yellow taxi/ }).click();
  await expect(page.getByLabel("Map legend")).toContainText("completed yellow-taxi pickups / hour");

  // Accessible table alternative.
  await page.getByRole("button", { name: "Table" }).click();
  await expect(page.getByRole("table")).toBeVisible();
  await page.getByRole("button", { name: "Hide table" }).click();

  if (await page.getByRole("button", { name: "Hide filters" }).isVisible()) {
    await page.getByRole("button", { name: "Hide filters" }).click();
  }
  await page.waitForTimeout(1500);
  if (shots) await page.screenshot({ path: `${shots}/pulse-${info.project.name}.png` });

  // Data quality and method pages.
  await page.goto("/#quality");
  await expect(page.getByRole("heading", { name: "Data quality" })).toBeVisible();
  await page.goto("/#method");
  await expect(page.getByRole("heading", { name: "How City Pulse is built" })).toBeVisible();

  expect(errors, errors.join("\n")).toEqual([]);
});

test("dark theme renders", async ({ page }, info) => {
  await page.goto("/#pulse");
  await page.getByRole("button", { name: /Switch to dark theme/ }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.waitForFunction(() => !document.body.innerText.includes("Loading map…"), null, { timeout: 30_000 });
  await page.waitForTimeout(2000);
  if (shots) await page.screenshot({ path: `${shots}/pulse-dark-${info.project.name}.png` });
});

test("Forecast Studio shows backtest chart, metrics and the frozen live forecast", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/#forecast");
  await expect(page.getByRole("heading", { name: "Forecast Studio" })).toBeVisible();
  await expect(page.getByRole("img", { name: /day-ahead forecasts/ })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("heading", { name: /How good is it/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: /^Gradient boosting(best)?$/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Live track record/ })).toBeVisible();
  await page.getByLabel("Zone").selectOption({ label: "Midtown Center — Manhattan" });
  await expect(page.getByText("90% interval", { exact: true })).toBeVisible();
  if (shots) await page.screenshot({ path: `${shots}/forecast-${info.project.name}.png`, fullPage: true });
  expect(errors, errors.join("\n")).toEqual([]);
});

test("Flows shows schematic links, top-pairs table and borough matrix", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/#flows");
  await expect(page.getByRole("heading", { name: "Flows" })).toBeVisible();
  await expect(page.getByText(/schematic links between zone centres, not street routes/)).toBeVisible();
  await expect(page.locator("table tbody tr").first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("img", { name: "Average trips per day between boroughs" })).toBeVisible();
  await page.getByRole("button", { name: "Weekends" }).click();
  await expect(page.getByText(/trips per weekend day/)).toBeVisible();
  await page.waitForTimeout(1500);
  if (shots) await page.screenshot({ path: `${shots}/flows-${info.project.name}.png`, fullPage: true });
  expect(errors, errors.join("\n")).toEqual([]);
});

test("Congestion-pricing page states its verdicts and limits", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/#policy");
  await expect(page.getByRole("heading", { name: /Congestion pricing/ })).toBeVisible();
  await expect(page.getByRole("img", { name: "Monthly median trip pace by group" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole("img", { name: /confidence intervals/ })).toBeVisible();
  await expect(page.getByText("Trip pace is not traffic speed", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: /Yellow taxi/ }).click();
  await expect(page.getByText(/Placebo check fails — no causal claim/)).toBeVisible();
  if (shots) await page.screenshot({ path: `${shots}/policy-${info.project.name}.png`, fullPage: true });
  expect(errors, errors.join("\n")).toEqual([]);
});
