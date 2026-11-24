import { expect, test } from "@playwright/test";

test("owner creates a private invite that a manager can use with their own email", async ({browser, page, baseURL}) => {
  test.skip(process.env.WORKSPACE_SIGNUP_ENABLED !== "true", "Requires workspace signup mode");
  await page.context().addCookies([{name: "manager-session", value: "fixture-owner", url: baseURL!}]);
  await page.route("**/auth/security", route => route.fulfill({json: {
    workspace_owner: true, mfa_enabled: false, recovery_codes_remaining: 0,
  }}));
  await page.route("**/api/workspace/managers", route => route.fulfill({json: {managers: []}}));
  const email = "colleague@example.test";
  const invitation = "team_" + "a".repeat(43);
  await page.route("**/api/workspace/manager-invitations", async route => {
    expect(route.request().postDataJSON()).toEqual({email});
    await route.fulfill({status: 201, json: {invitation_token: invitation}});
  });
  await page.goto("/security");
  await page.getByLabel("Manager email").fill(email);
  await page.getByRole("button", {name: "Create invite link"}).click();
  const link = page.getByLabel("Private invite link");
  await expect(link).toHaveValue(`${baseURL}/join#invite=${invitation}`);

  const manager = await browser.newPage({viewport: {width: 390, height: 844}});
  try {
    await manager.route("**/auth/signup", async route => {
      expect(route.request().postDataJSON()).toMatchObject({email, invitation_token: invitation});
      await route.fulfill({status: 403, json: {error: {message: "Fixture signup submitted"}}});
    });
    await manager.goto(await link.inputValue());
    await manager.getByRole("link", {name: "Create an account"}).click();
    await manager.getByRole("button", {name: "Create account"}).click();
    await manager.getByLabel("Your name").fill("Fictional manager");
    await manager.getByLabel("Email", {exact: true}).fill(email);
    await manager.getByLabel("Password", {exact: false}).fill("fictional-manager-password");
    await manager.getByRole("button", {name: "Create account"}).click();
    await expect(manager.getByText("Fixture signup submitted", {exact: true})).toBeVisible();
    expect(new URL(manager.url()).hash).toBe("");
  } finally {
    await manager.close();
  }
});

test("an existing manager accepts an invitation and chooses the new company", async ({page, baseURL}) => {
  test.skip(process.env.WORKSPACE_SIGNUP_ENABLED !== "true", "Requires workspace signup mode");
  const invitation = "team_" + "b".repeat(43);
  await page.context().addCookies([{name: "manager-session", value: "fixture-account", url: baseURL!}]);
  await page.route("**/auth/join", async route => {
    expect(route.request().postDataJSON()).toEqual({invitation_token: invitation});
    await route.fulfill({json: {workspace_id: "invited"}});
  });
  await page.route("**/auth/workspaces", route => route.fulfill({json: {
    current: "original", items: [{id: "original", name: "Original Company"}, {id: "invited", name: "Second Company"}],
  }}));
  await page.route("**/auth/switch-workspace", async route => {
    expect(route.request().postDataJSON()).toEqual({workspace_id: "invited"});
    await route.fulfill({json: {ok: true}});
  });
  await page.goto(`/join#invite=${invitation}`);
  await page.getByRole("button", {name: "Accept invitation"}).click();
  await expect(page.getByText("You joined the workspace.")).toBeVisible();
  expect(new URL(page.url()).hash).toBe("");
  await page.getByRole("link", {name: "Choose workspace"}).click();
  await expect(page.getByText("Original Company · Current")).toBeVisible();
  await page.getByRole("button", {name: "Second Company"}).click();
  await expect(page).toHaveURL(/\/dashboard$/);
});
