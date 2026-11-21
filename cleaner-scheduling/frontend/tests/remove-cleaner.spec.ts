import { expect, test } from "@playwright/test";

test("manager removes a cleaner with confirmation and preserves the history explanation", async ({page, baseURL}) => {
  await page.context().addCookies([{name: "cleaning-dev-role", value: "manager", url: baseURL!}]);
  let removed = false; let requestBody: unknown;
  await page.route("**/api/cleaners", route => route.fulfill({json: {items: removed ? [] : [{id: "example-cleaner", version: 3, display_name: "Example Cleaner", sms_consent_status: "pending", phone_stored: true, email_stored: false, reading_languages: [], preferred_language: null}]}}));
  await page.route("**/api/properties*", route => route.fulfill({json: {items: [], next_cursor: null}}));
  await page.route("**/api/setup", route => route.fulfill({json: {business_date: "2026-11-10"}}));
  await page.route("**/api/cleaners/example-cleaner/remove", route => {
    removed = true; requestBody = route.request().postDataJSON();
    return route.fulfill({json: {active: false, released: 1, needs_review: 0}});
  });
  await page.goto("/cleaners");
  await page.getByRole("button", {name: "Remove Example Cleaner", exact: true}).click();
  const confirmation = page.getByRole("group", {name: "Remove cleaner"});
  await expect(confirmation).toContainText("Past work stays in the history");
  await confirmation.getByRole("button", {name: "Keep cleaner"}).click();
  expect(removed).toBe(false);
  await page.getByRole("button", {name: "Remove Example Cleaner", exact: true}).click();
  await confirmation.getByRole("button", {name: "Confirm removal"}).click();
  await expect(page.getByRole("status")).toContainText("Cleaner removed");
  expect(requestBody).toEqual({expected_version: 3});
  await expect(page.getByRole("button", {name: "Remove Example Cleaner", exact: true})).toHaveCount(0);
  await expect(page.getByRole("listitem").filter({hasText: "Example Cleaner"})).toHaveCount(0);
});

test("viewers have no removal controls", async ({page, baseURL}) => {
  await page.context().addCookies([{name: "cleaning-dev-role", value: "viewer", url: baseURL!}]);
  await page.route("**/api/cleaners", route => route.fulfill({json: {items: [{id: "example-cleaner", version: 1, display_name: "Example Cleaner", sms_consent_status: "pending", phone_stored: true, email_stored: false, reading_languages: [], preferred_language: null}]}}));
  await page.route("**/api/properties*", route => route.fulfill({json: {items: [], next_cursor: null}}));
  await page.route("**/api/setup", route => route.fulfill({json: {business_date: "2026-11-10"}}));
  await page.goto("/cleaners");
  await expect(page.getByRole("listitem").filter({hasText: "Example Cleaner"})).toBeVisible();
  await expect(page.getByRole("button", {name: /Remove/})).toHaveCount(0);
});
