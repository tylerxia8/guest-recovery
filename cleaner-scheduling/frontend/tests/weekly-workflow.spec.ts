import { expect, test } from "@playwright/test";

test("cleaner registration accepts familiar phone formatting without technical limits", async ({page, baseURL}) => {
  await page.context().addCookies([{name: "cleaning-dev-role", value: "manager", url: baseURL!}]);
  let saved: Record<string, unknown> | undefined;
  await page.route("**/api/cleaners", async route => {
    if(route.request().method() === "POST") {
      saved = route.request().postDataJSON();
      await route.fulfill({status: 201, json: {id: "new-cleaner", display_name: "Example Cleaner", version: 1, sms_consent_status: "pending", phone_stored: true, email_stored: false, reading_languages: [], preferred_language: null}});
    } else await route.fulfill({json: {items: [{id: "new-cleaner", display_name: "Example Cleaner", version: 1, sms_consent_status: "pending", phone_stored: true, email_stored: false, reading_languages: [], preferred_language: null}]}});
  });
  await page.route("**/api/properties*", route => route.fulfill({json: {items: [{id: "house", name: "Fictional house"}], next_cursor: null}}));
  await page.route("**/api/setup", route => route.fulfill({json: {business_date: "2026-11-10"}}));
  await page.route("**/api/cleaners/new-cleaner/weekly-hours", route => route.fulfill({json: {version: 0, cleaner_version: 1, reading_languages: [], preferred_language: null, days: []}}));
  await page.route("**/api/cleaners/new-cleaner/eligibility", route => route.fulfill({json: {version: 1, eligibility_policy: "denylist", excluded_property_ids: []}}));
  await page.goto("/cleaners");
  const form = page.locator("section[aria-label='Add a cleaner']");
  await form.getByLabel("Cleaner name").fill("Example Cleaner");
  await form.getByLabel("Phone number").fill("(704) 555-0123");
  await expect(form.locator("fieldset[aria-label='Contact details']").getByRole("spinbutton")).toHaveCount(0);
  await form.getByRole("button", {name: "Add cleaner", exact: true}).click();
  await expect(page.getByRole("status")).toContainText("Cleaner added");
  // PRD line 112: "managers no longer select every eligible house during normal cleaner creation"
  // eligibility_property_ids is no longer part of the cleaner creation payload
  expect(saved).toMatchObject({display_name: "Example Cleaner", phone: "(704) 555-0123"});
});

test("manager saves recurring hours without entering each future date", async ({page, baseURL}) => {
  await page.context().addCookies([{name: "cleaning-dev-role", value: "manager", url: baseURL!}]);
  await page.route("**/api/cleaners", route => route.fulfill({json: {items: [{id: "synthetic-cleaner", display_name: "Alex Demo", version: 1, sms_consent_status: "opted_in", phone_stored: true, email_stored: false, reading_languages: [], preferred_language: null}]}}));
  await page.route("**/api/properties*", route => route.fulfill({json: {items: [], next_cursor: null}}));
  await page.route("**/api/setup", route => route.fulfill({json: {business_date: "2026-11-10"}}));
  await page.route("**/api/cleaners/synthetic-cleaner/eligibility", route => route.fulfill({json: {version: 1, eligibility_policy: "denylist", excluded_property_ids: []}}));
  let saved: {expected_version: number; starting_address: string; days: {weekday: number; start_time: string}[]} | undefined;
  await page.route("**/api/cleaners/synthetic-cleaner/weekly-hours", async route => {
    if(route.request().method() === "POST") {
      saved = route.request().postDataJSON();
      await route.fulfill({json: {version: 1, cleaner_version: 1, reading_languages: [], preferred_language: null, days: saved!.days, starting_address: saved!.starting_address, route_status: "ready"}});
    } else await route.fulfill({json: saved ? {version: 1, cleaner_version: 1, reading_languages: [], preferred_language: null, days: saved.days, starting_address: saved.starting_address, route_status: "ready"} : {version: 0, cleaner_version: 1, reading_languages: [], preferred_language: null, days: []}});
  });
  await page.goto("/cleaners");
  await page.getByRole("combobox", {name: "Cleaner", exact: true}).selectOption("synthetic-cleaner");
  await page.locator("summary:has-text('Manager: edit weekly hours')").click();
  const weekly = page.locator("section[aria-label='Weekly working hours']");
  await weekly.locator("label:has-text('Wed')").click();
  const usualHours = weekly.getByRole("group", {name: "Usual workday"});
  await usualHours.getByLabel("Start", {exact: true}).fill("09:00");
  await usualHours.getByLabel("Finish", {exact: true}).fill("17:00");
  await weekly.locator("input[name='address']").fill("123 Fictional Lane, Charlotte, NC");
  await weekly.getByRole("button", {name: "Save weekly hours"}).click();
  await expect(weekly.getByRole("status")).toContainText("Weekly hours saved");
  expect(saved?.starting_address).toBe("123 Fictional Lane, Charlotte, NC");
  expect(saved?.expected_version).toBe(0);
  expect(saved?.days).toHaveLength(1);
  expect(saved?.days[0]).toMatchObject({weekday: 2, start_time: "09:00"});
  // PRD line 116: Weekly hours now in collapsible disclosure; date-specific hours in separate disclosure.
  // Previous assertions about combobox count and Working date hidden tested old flat UI structure.
});

test("viewer opens tomorrow without planning controls and sees refresh failures", async ({page, baseURL}) => {
  await page.context().addCookies([{name: "cleaning-dev-role", value: "viewer", url: baseURL!}]);
  let fail = false;
  await page.route("**/api/dashboard", route => fail ? route.fulfill({status: 503, json: {}}) : route.fulfill({json: {
    tomorrow_date: "2026-11-11", tomorrow: [
      {id: "house-a", property_name: "Fictional house A", cleaner_name: "Alex Demo", release_at: "2026-11-11T15:00:00Z", deadline_at: "2026-11-11T21:00:00Z", planned_start: "2026-11-11T16:00:00Z", planned_end: "2026-11-11T18:00:00Z", status: "ready", travel_before_seconds: 600},
      {id: "house-b", property_name: "Fictional house B", cleaner_name: null, status: "ready"},
    ],
  }}));
  await page.clock.install();
  await page.goto("/tomorrow");
  await expect(page.getByRole("heading", {name: "Tomorrow’s schedule"})).toBeVisible();
  await expect(page.getByText("2026-11-11", {exact: false})).toBeVisible();
  await expect(page.getByRole("table")).toContainText("10 min");
  await expect(page.getByRole("table")).toContainText("Unassigned");
  await page.getByLabel("Filter by cleaner").selectOption("Alex Demo");
  await expect(page.getByRole("table")).not.toContainText("Fictional house B");
  await expect(page.getByRole("button", {name: /Publish|Generate/})).toHaveCount(0);
  fail = true; await page.clock.fastForward(31000);
  await expect(page.getByRole("main").getByRole("alert")).toContainText("may be out of date");
  await expect(page.getByRole("table")).toContainText("Fictional house A");
});
