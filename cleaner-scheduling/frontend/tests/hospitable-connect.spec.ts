import { expect, test } from "@playwright/test";

test("live connection stays unavailable outside the guarded demo", async ({page, request}) => {
  test.skip(process.env.LOCAL_DEMO === "1", "Regular development server required");
  await page.goto("/");
  await expect(page.getByRole("button", {name: "Connect to Hospitable", exact: true})).toBeDisabled();
  await expect(page.getByLabel("Sign in as")).toBeHidden();
  const response = await request.post("/session", {data: {connection: "hospitable", role: "owner"}});
  expect(response.status()).toBe(503);
  expect(response.headers()["set-cookie"]).toBeUndefined();
});

test("connection rejects a different origin without creating a session", async ({request}) => {
  const response = await request.post("/session", {
    headers: {Origin: "https://untrusted.invalid"}, data: {connection: "hospitable"},
  });
  expect(response.status()).toBe(403);
  expect(response.headers()["set-cookie"]).toBeUndefined();
});
