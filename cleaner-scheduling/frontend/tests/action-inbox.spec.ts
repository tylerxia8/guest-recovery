import { expect, test } from "@playwright/test";

test("manager claims an inbox item on a phone", async ({page, baseURL}) => {
  test.skip(process.env.WORKSPACE_SIGNUP_ENABLED !== "true", "Requires workspace signup mode");
  const id = "a0000000-0000-4000-8000-000000000001";
  await page.setViewportSize({width: 390, height: 844});
  await page.context().addCookies([{name: "manager-session", value: "fixture-manager", url: baseURL!}]);
  await page.route("**/api/action-inbox", route => route.fulfill({json: {
    items: [{
      id, source: "maintenance", title: "Repair #5", property: "Fictional Lodge",
      next_action: "Assign an available repair worker", due_at: null, urgent: true, href: "/maintenance",
    }],
    claims: [], managers: [{subject: "manager:test", name: "Fixture Manager"}],
    current_manager: "manager:test", unavailable: ["Guest Messages"],
  }}));
  await page.route("**/api/workspace/action-inbox/claim", async route => {
    expect(route.request().postDataJSON()).toEqual({
      source: "maintenance", item_id: id, assignee_subject: "manager:test", version: 0,
    });
    await route.fulfill({json: {version: 1, assignee_subject: "manager:test"}});
  });
  await page.goto("/action-inbox");
  await expect(page.getByText("Unavailable queue: Guest Messages.")).toBeVisible();
  await expect(page.getByRole("heading", {name: "Repair #5"})).toBeVisible();
  await page.getByRole("button", {name: "Claim"}).click();
  await expect(page.getByLabel("Assigned to")).toHaveValue("manager:test");
  await expect(page.getByRole("button", {name: "Claim"})).toHaveCount(0);
});
