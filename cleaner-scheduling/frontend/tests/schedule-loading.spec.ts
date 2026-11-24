import { expect, test } from "@playwright/test";

test("schedule controls cannot submit before JavaScript is ready", async ({browser, baseURL}) => {
  const context = await browser.newContext({javaScriptEnabled: false, baseURL});
  try {
    await context.addCookies([{name: "cleaning-dev-role", value: "manager", url: baseURL!}]);
    const page = await context.newPage();
    await page.goto("/schedules");
    await expect(page.getByRole("button", {name: "Load schedule"})).toBeDisabled();
    await expect(page.getByRole("button", {name: "Generate preview"})).toBeDisabled();
    await expect(page.getByLabel("Schedule date")).toBeDisabled();
  } finally {
    await context.close();
  }
});

test("a direct visit loads the schedule through the API once ready", async ({page, baseURL}) => {
  await page.context().addCookies([{name: "cleaning-dev-role", value: "manager", url: baseURL!}]);
  await page.route("**/api/cleaners", route => route.fulfill({json: {items: []}}));
  await page.route("**/api/dashboard?*", route => route.fulfill({json: {today: [], tomorrow: []}}));
  await page.route("**/api/demo/assignments", route => route.fulfill({json: {items: []}}));
  await page.route("**/api/schedules?*", route => route.fulfill({json: {
    active_run: {id: "synthetic-run", version: 1, service_date: "2026-11-10", status: "published",
      input_fingerprint: "synthetic", day_revision: 1, solver_status: "feasible",
      uncovered: [], candidate_plan: {placed: []}},
  }}));
  await page.goto("/schedules");
  const [response] = await Promise.all([
    page.waitForResponse(response => response.url().includes("/api/schedules?")),
    page.getByRole("button", {name: "Load schedule"}).click(),
  ]);
  expect(response.ok()).toBeTruthy();
  await expect(page.getByRole("region", {name: "Schedule plan"})).toContainText("0 assignments");
});

test("schedule preview stays readable at desktop and phone widths", async ({ page, baseURL }) => {
  await page.context().addCookies([{ name: "cleaning-dev-role", value: "manager", url: baseURL! }]);
  await page.route("**/api/cleaners", route => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/dashboard?*", route => route.fulfill({ json: { today: [], tomorrow: [] } }));
  await page.route("**/api/demo/assignments", route => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/schedules?*", route => route.fulfill({ json: {
    active_run: {
      id: "synthetic-run", version: 1, service_date: "2026-11-10", status: "published",
      input_fingerprint: "synthetic", day_revision: 1, solver_status: "feasible", uncovered: [],
      candidate_plan: { placed: [{
        task_id: "Fictional Willow Cottage", cleaner_id: "demo",
        start: "2026-11-10T16:00:00Z", end: "2026-11-10T17:30:00Z", travel_before_seconds: 600,
      }] },
    },
  } }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/schedules");
  await page.getByRole("button", { name: "Load schedule" }).click();
  const preview = page.getByRole("region", { name: "Schedule preview assignments" });
  await expect(preview.getByRole("table")).toContainText("Fictional Willow Cottage");

  for (const width of [1440, 768, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
  await preview.focus();
  await expect(preview).toBeFocused();
  await page.keyboard.press("ArrowRight");
  await expect.poll(() => preview.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
});
