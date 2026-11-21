import { expect, test } from "@playwright/test";

test("manager uses one source for houses and bookings without duplicate entry", async ({page}) => {
  const url = `http://127.0.0.1:${process.env.PLAYWRIGHT_PORT ?? "3000"}`;
  await page.context().addCookies([{name: "cleaning-dev-role", value: "manager", url}]);
  await page.route("**/api/setup*", route => route.fulfill({json: {
    business_date: "2026-11-10", properties: 1, properties_needing_cleaning_time: 0,
    cleaners: 1, cleaners_without_eligibility: 0, cleaners_without_workday: 0,
    cleaners_without_consent: 0, policy_approved: true, source_fresh: true, blockers: [],
    cleaning_minutes_by_bedrooms: {},
    reservation_window: {from_time: "2026-11-10T05:00:00Z", to_time: "2026-12-11T05:00:00Z"},
  }}));
  await page.route("**/api/properties*", route => route.fulfill({json: {items: [{id: "demo-house", name: "Fictional source house"}], next_cursor: null}}));
  await page.route("**/api/reservations?*", route => route.fulfill({json: {items: [{id: "demo-booking", property_id: "demo-house", status: "active", checkin_at: "2026-11-10T21:00:00Z", checkout_at: "2026-11-11T15:00:00Z"}]}}));
  await page.route("**/api/demo/account", route => route.fulfill({json: {account_name: "Fictional proxy", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "mock"}}));
  await page.route("**/api/daily-operations*", route => route.fulfill({json: {
    service_date: "2026-11-10", business_date: "2026-11-10", min_date: "2026-11-01", max_date: "2026-12-11",
    occupancy_as_of: "2026-11-10T12:00:00Z", mode: "mock",
    houses: [{id: "demo-house", name: "Fictional source house"}],
    occupied: [], check_ins: [], check_outs: [{id: "demo-booking", property_name: "Fictional source house", checkin_at: "2026-11-10T21:00:00Z", checkout_at: "2026-11-11T15:00:00Z"}],
    turnovers: [], tasks: [],
    sync_health: {status: "succeeded", stale: false, source_as_of: "2026-11-10T12:00:00Z"},
  }}));
  await page.goto("/setup");
  await expect(page.getByText("0 need a cleaning time before they can be scheduled.", {exact: false})).toBeVisible();
  const nav = page.getByRole("navigation", {name: "Primary"});
  await expect(nav.getByRole("link")).toHaveCount(3);
  await expect(nav.getByRole("link", {name: "Properties", exact: true})).toHaveCount(0);
  await page.getByRole("link", {name: "Hospitable", exact: true}).click();
  await page.locator("summary:has-text('Connection details')").click();
  await expect(page.getByText("1 active houses", {exact: false})).toBeVisible({timeout: 10000});
  await expect(page.getByRole("button", {name: "Add synthetic bookings"})).toBeHidden();
  await expect(page.getByText("Mock reservation source", {exact: false})).toBeVisible();
});
