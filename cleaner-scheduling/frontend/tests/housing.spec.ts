import { expect, test } from "@playwright/test";

test("public housing context distinguishes assessments from demo operations", async ({page}) => {
  const url = `http://127.0.0.1:${process.env.PLAYWRIGHT_PORT ?? "3000"}`;
  await page.context().addCookies([{name: "cleaning-dev-role", value: "manager", url}]);
  await page.route("**/api/properties*", route => route.fulfill({json: {items: [{
    id: "fixture", name: "Fictional Charlotte test house", external_id: "fixture", active: true,
    version: 1, bedrooms: 3, duration_override_minutes: 90,
  }], next_cursor: null}}));
  await page.route("**/api/demo/housing", route => route.fulfill({json: {
    source_url: "https://example.org/public-records", retrieved_at: "2026-11-07T00:00:00Z",
    geography: "Fictional test area", items: [{property_id: "fixture", address: "Fictional Charlotte test house",
      property_type: "Single-Family", assessment_year: 2023, assessed_value: 500000}],
  }}));
  await page.goto("/properties");
  await expect(page.getByText("These homes are not verified rentals or managed properties.", {exact: false})).toBeVisible();
  await expect(page.getByText("Public record: Single-Family; 2023 assessed value $500,000.", {exact: false})).toBeVisible();
  await expect(page.getByText("Bedroom count and cleaning duration above are demo assumptions.", {exact: false})).toBeVisible();
  await expect(page.getByRole("link", {name: "City housing data"})).toHaveAttribute("href", "https://example.org/public-records");
});
