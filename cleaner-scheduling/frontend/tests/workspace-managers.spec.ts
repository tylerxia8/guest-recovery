import { expect, test } from "@playwright/test";

test("owner creates a private invite that a manager can use with their own email", async ({browser, page, baseURL}) => {
  test.skip(process.env.WORKSPACE_SIGNUP_ENABLED !== "true", "Requires workspace signup mode");
  await page.context().addCookies([{name: "manager-session", value: "fixture-owner", url: baseURL!}]);
  await page.route("**/auth/security", route => route.fulfill({json: {
    workspace_owner: true, mfa_enabled: false, recovery_codes_remaining: 0,
  }}));
  await page.route("**/api/workspace/managers", route => route.fulfill({json: {managers: []}}));
  const email = "colleague@example.test";
  const invitation = "team_fictional-invitation-for-browser-testing";
  await page.route("**/api/workspace/manager-invitations", async route => {
    expect(route.request().postDataJSON()).toEqual({email});
    await route.fulfill({status: 201, json: {invitation_token: invitation}});
  });
  await page.goto("/security");
  await page.getByLabel("Manager email").fill(email);
  await page.getByRole("button", {name: "Create invite link"}).click();
  const link = page.getByLabel("Private invite link");
  await expect(link).toHaveValue(`${baseURL}/signup#invite=${invitation}`);

  const manager = await browser.newPage({viewport: {width: 390, height: 844}});
  try {
    await manager.route("**/auth/signup", async route => {
      expect(route.request().postDataJSON()).toMatchObject({email, invitation_token: invitation});
      await route.fulfill({status: 403, json: {error: {message: "Fixture signup submitted"}}});
    });
    await manager.goto(await link.inputValue());
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
