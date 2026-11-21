import { createHmac } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

const API_ORIGIN = process.env.CLEANING_API_ORIGIN ?? "http://127.0.0.1:8000";
const WEB_ORIGIN = `http://127.0.0.1:${process.env.PLAYWRIGHT_PORT ?? "3000"}`;
const SECRET = "test-only-mock-secret-not-a-credential";

function devToken(subject: string, roles: string[]): string {
  const header = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" })).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const payload = Buffer.from(
    JSON.stringify({
      sub: subject,
      roles,
      iss: "http://localhost/mock-issuer",
      aud: "cleaning-local",
      iat: now,
      exp: now + 600,
    }),
  ).toString("base64url");
  const data = `${header}.${payload}`;
  const signature = createHmac("sha256", SECRET).update(data).digest("base64url");
  return `${data}.${signature}`;
}

async function signIn(page: Page, token: string, role: string) {
  await page.context().addCookies([
    { name: "cleaning-dev-token", value: token, url: WEB_ORIGIN, httpOnly: true, sameSite: "Lax" },
    { name: "cleaning-dev-role", value: role, url: WEB_ORIGIN, httpOnly: false, sameSite: "Lax" },
  ]);
}

test("manager dashboard renders live mock panels and viewer cannot resolve", async ({ page, request }) => {
  const manager = devToken("synthetic-manager", ["manager"]);
  const viewer = devToken("synthetic-viewer", ["viewer"]);
  const denied = await request.get(`${API_ORIGIN}/v1/dashboard?service_date=2026-11-10`);
  expect(denied.status()).toBe(401);
  const viewerApi = await request.get(`${API_ORIGIN}/v1/dashboard?service_date=2026-11-10`, {
    headers: { Authorization: `Bearer ${viewer}` },
  });
  expect(viewerApi.ok()).toBeTruthy();
  const viewerBody = (await viewerApi.json()) as Record<string, unknown>;
  expect(viewerBody.payment_queue_count).toBeUndefined();
  expect(JSON.stringify(viewerBody)).not.toContain("amount_cents");

  await signIn(page, manager, "manager");
  await page.goto("/dashboard");
  await page.waitForSelector("table", {timeout: 10000});
  await expect(page.locator("h1:has-text('cleaning')")).toBeVisible();
  await expect(page.locator("section.today-cleaning-board")).toBeVisible();
  await expect(page.locator("th:has-text('Clean by')")).toBeVisible();

  await page.goto("/properties");
  await expect(page.getByRole("heading", { name: "Properties" })).toBeVisible();
  await expect(page.getByRole("listitem").filter({hasText: "Synthetic Property 001"})).toBeVisible();

  await page.goto("/cleaners");
  await expect(page.getByRole("heading", { name: "Cleaners", exact: true })).toBeVisible();

  for (const path of ["/settings", "/payments"]) {
    const response = await page.goto(path);
    expect(response?.status()).toBe(404);
  }

  await signIn(page, viewer, "viewer");
  await page.goto("/dashboard");
  await expect(page.getByRole("link", {name: "Payments", exact: true})).toHaveCount(0);
  await expect(page.getByRole("link", {name: "Settings", exact: true})).toHaveCount(0);
  await page.goto("/exceptions");
  await expect(page.getByRole("button", { name: "Resolve" })).toHaveCount(0);
});

test("exception queue shows an empty state and a denied write stays on the API", async ({ page, request }) => {
  const viewer = devToken("synthetic-viewer", ["viewer"]);
  const forbidden = await request.patch(`${API_ORIGIN}/v1/exceptions/00000000-0000-0000-0000-000000000001`, {
    headers: { Authorization: `Bearer ${viewer}`, "If-Match": '"1"', "Content-Type": "application/json" },
    data: { owner_subject: "synthetic-viewer" },
  });
  expect(forbidden.status()).toBe(403);
  const listed = await request.get(`${API_ORIGIN}/v1/exceptions?status=open`, {
    headers: { Authorization: `Bearer ${viewer}` },
  });
  const items = ((await listed.json()) as { items: unknown[] }).items;
  await signIn(page, viewer, "viewer");
  await page.goto("/exceptions");
  await expect(page.getByRole("heading", { name: "Exception queue" })).toBeVisible();
  if (items.length === 0) {
    await expect(page.locator(".empty-state")).toBeVisible();
    await expect(page.locator(".empty-state-title")).toContainText("No exceptions in this filter");
  }
  await expect(page.getByRole("button", { name: "Resolve" })).toHaveCount(0);
});

test("exception actions submit the selected action and refresh the queue", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");
  let resolved = false;
  let version = 1;
  let writes = 0;
  await page.route("**/api/exceptions**", async route => {
    if (route.request().method() === "PATCH") {
      const payload = route.request().postDataJSON() as {status: string; owner_subject?: string; resolution_reason?: string};
      if (writes === 0) {
        expect(payload).toEqual({ status: "acknowledged", owner_subject: "synthetic-manager" });
      } else {
        expect(payload).toEqual({ status: "resolved", resolution_reason: "Confirmed directly" });
        resolved = true;
      }
      writes++; version++;
      await route.fulfill({ json: { ok: true } });
    } else {
      await route.fulfill({ json: { items: resolved ? [] : [{ id: "demo-exception", code: "MISSING_ACK", version, status: writes ? "acknowledged" : "open", owner_subject: writes ? "synthetic-manager" : null, severity: "warning" }] } });
    }
  });
  await page.goto("/exceptions");
  await page.getByLabel("Owner for MISSING_ACK").fill("synthetic-manager");
  await page.getByRole("button", { name: "Assign owner" }).click();
  await expect(page.getByText("MISSING_ACK warning acknowledged owner synthetic-manager")).toBeVisible();
  await page.getByLabel("Reason for MISSING_ACK").fill("Confirmed directly");
  await page.getByRole("button", { name: "Resolve", exact: true }).click();
  await expect(page.locator(".empty-state")).toBeVisible();
  await expect(page.locator(".empty-state-title")).toContainText("No exceptions in this filter");
  expect(writes).toBe(2);
});


test("dashboard refreshes automatically and labels retained data after a failed refresh", async ({page}) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");
  await page.route("**/api/demo/account", route => route.fulfill({json: {account_name: "Synthetic test account", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test"}}));
  let label = "First synthetic turnover";
  let fail = false;
  await page.route("**/api/dashboard*", route => {
    if(fail) return route.fulfill({status: 503, contentType: "application/json", body: JSON.stringify({error: {message: "Source temporarily unavailable"}})});
    return route.fulfill({json: {
      service_date: "2026-11-10", tomorrow_date: "2026-11-11",
      today: [{id: "synthetic-poll-task", property_name: label, cleaner_name: null, planned_start: null, planned_end: null, acknowledged: false, status: "ready", pending: false, deadline_at: "2026-11-10T21:00:00Z"}],
      tomorrow: [], availability: {}, assignment_counts: {}, ack_counts: {acknowledged: 0, awaiting: 0}, completion_counts: {completed: 0, open: 1},
      exceptions: [], sync_health: {status: "succeeded", stale: false, publication_blocked: false}, sms_delivery_counts: {},
      pending_deadline_count: 0, uncovered_count: 0, late_count: 0, dispatch_enabled: false,
    }});
  });
  await page.clock.install();
  await page.goto("/dashboard");
  await expect(page.getByText("First synthetic turnover", {exact: true})).toBeVisible();
  label = "Updated synthetic turnover";
  await page.clock.fastForward(30000);
  await expect(page.getByText("Updated synthetic turnover", {exact: true})).toBeVisible();
  fail = true;
  await page.clock.fastForward(30000);
  await expect(page.getByRole("main").getByRole("alert")).toContainText("displayed information may be out of date");
  await expect(page.getByText("Updated synthetic turnover", {exact: true})).toBeVisible();
});
