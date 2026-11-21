import { createHmac } from "node:crypto";
import { test, expect, type Page } from "@playwright/test";

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

test("manifest.webmanifest is served with correct content", async ({ request }) => {
  const response = await request.get("/manifest.webmanifest");
  expect(response.status()).toBe(200);
  
  const manifest = await response.json();
  expect(manifest.name).toBe("Turnover");
  expect(manifest.theme_color).toBe("#253122");
  expect(manifest.background_color).toBe("#f7f6f2");
  expect(manifest.icons).toHaveLength(3);
  expect(manifest.icons[0].src).toBe("/brand/icon-192.png");
});

test("favicon.svg is served", async ({ request }) => {
  const response = await request.get("/favicon.svg");
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toContain("image/svg+xml");
});

test("brand icons are served", async ({ request }) => {
  const icons = [
    "/brand/icon-192.png",
    "/brand/icon-512.png",
    "/brand/icon-maskable-512.png",
    "/brand/apple-touch-icon.png",
    "/brand/turnover-mark.svg",
    "/brand/turnover.png",
  ];
  
  for (const icon of icons) {
    const response = await request.get(icon);
    expect(response.status(), `${icon} should be served`).toBe(200);
  }
});

test("empty state illustrations are served", async ({ request }) => {
  const illustrations = [
    "/illustrations/G5-empty-dashboard-light.svg",
    "/illustrations/G6-empty-cleaners-light.svg",
    "/illustrations/G7-empty-houses-light.svg",
    "/illustrations/G8-empty-exceptions-light.svg",
    "/illustrations/G9-portal-no-jobs-light.svg",
    "/illustrations/G10-onboarding-welcome-light.svg",
    "/illustrations/G11-manager-first-run-light.svg",
    "/illustrations/G12-success-light.svg",
  ];
  
  for (const illustration of illustrations) {
    const response = await request.get(illustration);
    expect(response.status(), `${illustration} should be served`).toBe(200);
    expect(response.headers()["content-type"]).toContain("image/svg+xml");
  }
});

test("header renders with brand-symbol image", async ({ page }) => {
  await signIn(page, devToken("test-manager", ["manager"]), "manager");
  await page.route("**/api/demo/account", route => route.fulfill({json: {account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: null, scope: "test"}}));
  await page.route("**/api/dashboard*", route => route.fulfill({json: {
    service_date: "2026-11-10", tomorrow_date: "2026-11-11", business_date: "2026-11-10", mode: "mock",
    today: [{id: "task-1", property_name: "Test Property", cleaner_name: "Test Cleaner", planned_start: null, planned_end: null, acknowledged: false, status: "ready", pending: false, deadline_at: "2026-11-10T21:00:00Z", release_at: "2026-11-10T14:00:00Z", cleaned: false, overdue: false}], tomorrow: [], overdue: [],
    availability: {}, assignment_counts: {}, ack_counts: {acknowledged: 0, awaiting: 0}, completion_counts: {completed: 0, open: 1},
    exceptions: [], sync_health: {status: "succeeded", stale: false, publication_blocked: false}, sms_delivery_counts: {},
    pending_deadline_count: 0, uncovered_count: 0, late_count: 0, dispatch_enabled: false,
  }}));
  await page.goto("/dashboard");
  await page.waitForSelector("table", {timeout: 10000});
  
  const brandSymbol = page.locator("header .brand-symbol");
  await expect(brandSymbol).toBeVisible();
  
  const tagName = await brandSymbol.evaluate((el) => el.tagName.toLowerCase());
  expect(tagName).toBe("img");
});

test("navigation renders with Lucide icons", async ({ page }) => {
  await signIn(page, devToken("test-manager", ["manager"]), "manager");
  await page.route("**/api/demo/account", route => route.fulfill({json: {account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: null, scope: "test"}}));
  await page.route("**/api/dashboard*", route => route.fulfill({json: {
    service_date: "2026-11-10", tomorrow_date: "2026-11-11", business_date: "2026-11-10", mode: "mock",
    today: [{id: "task-1", property_name: "Test Property", cleaner_name: "Test Cleaner", planned_start: null, planned_end: null, acknowledged: false, status: "ready", pending: false, deadline_at: "2026-11-10T21:00:00Z", release_at: "2026-11-10T14:00:00Z", cleaned: false, overdue: false}], tomorrow: [], overdue: [],
    availability: {}, assignment_counts: {}, ack_counts: {acknowledged: 0, awaiting: 0}, completion_counts: {completed: 0, open: 1},
    exceptions: [], sync_health: {status: "succeeded", stale: false, publication_blocked: false}, sms_delivery_counts: {},
    pending_deadline_count: 0, uncovered_count: 0, late_count: 0, dispatch_enabled: false,
  }}));
  await page.goto("/dashboard");
  await page.waitForSelector("table", {timeout: 10000});
  
  const navLinks = page.locator("nav a");
  const count = await navLinks.count();
  expect(count).toBeGreaterThanOrEqual(3);
  
  const firstNavLink = navLinks.first();
  const svg = firstNavLink.locator("svg");
  await expect(svg).toBeVisible();
});

test("dashboard empty state renders illustration with caption when no cleaning work", async ({ page }) => {
  await signIn(page, devToken("test-manager", ["manager"]), "manager");
  await page.route("**/api/demo/account", route => route.fulfill({json: {account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: null, scope: "test"}}));
  await page.route("**/api/dashboard*", route => route.fulfill({json: {
    service_date: "2026-11-10", tomorrow_date: "2026-11-11", business_date: "2026-11-10", mode: "mock",
    today: [], tomorrow: [], overdue: [],
    availability: {}, assignment_counts: {}, ack_counts: {acknowledged: 0, awaiting: 0}, completion_counts: {completed: 0, open: 0},
    exceptions: [], sync_health: {status: "succeeded", stale: false, publication_blocked: false}, sms_delivery_counts: {},
    pending_deadline_count: 0, uncovered_count: 0, late_count: 0, dispatch_enabled: false,
  }}));
  await page.goto("/dashboard");
  const emptyState = page.locator(".empty-state");
  await expect(emptyState).toBeVisible();
  const illustration = emptyState.locator("img.empty-state-illustration");
  await expect(illustration).toBeVisible();
  await expect(illustration).toHaveAttribute("src", "/illustrations/G5-empty-dashboard-light.svg");
  await expect(illustration).toHaveAttribute("alt", "");
  await expect(emptyState.locator(".empty-state-title")).toContainText("No cleaning work on this date");
});

test("exceptions empty state renders illustration with caption when no exceptions", async ({ page }) => {
  await signIn(page, devToken("test-manager", ["manager"]), "manager");
  await page.route("**/api/exceptions*", route => route.fulfill({json: {items: []}}));
  await page.goto("/exceptions");
  const emptyState = page.locator(".empty-state");
  await expect(emptyState).toBeVisible();
  const illustration = emptyState.locator("img.empty-state-illustration");
  await expect(illustration).toBeVisible();
  await expect(illustration).toHaveAttribute("src", "/illustrations/G8-empty-exceptions-light.svg");
  await expect(illustration).toHaveAttribute("alt", "");
  await expect(emptyState.locator(".empty-state-title")).toContainText("No exceptions in this filter");
});

test("cleaners empty state renders illustration with caption when no cleaners", async ({ page }) => {
  await signIn(page, devToken("test-manager", ["manager"]), "manager");
  await page.route("**/api/cleaners", route => route.fulfill({json: {items: []}}));
  await page.route("**/api/properties*", route => route.fulfill({json: {items: []}}));
  await page.route("**/api/setup", route => route.fulfill({json: {business_date: "2026-11-10"}}));
  await page.goto("/cleaners");
  const emptyState = page.locator(".empty-state");
  await expect(emptyState).toBeVisible();
  const illustration = emptyState.locator("img.empty-state-illustration");
  await expect(illustration).toBeVisible();
  await expect(illustration).toHaveAttribute("src", "/illustrations/G6-empty-cleaners-light.svg");
  await expect(illustration).toHaveAttribute("alt", "");
  await expect(emptyState.locator(".empty-state-title")).toContainText("No cleaners");
});

test("properties empty state renders illustration with caption when no houses", async ({ page }) => {
  await signIn(page, devToken("test-manager", ["manager"]), "manager");
  await page.route("**/api/properties*", route => route.fulfill({json: {items: []}}));
  await page.route("**/api/demo/housing", route => route.fulfill({json: null}));
  await page.goto("/properties");
  const emptyState = page.locator(".empty-state");
  await expect(emptyState).toBeVisible();
  const illustration = emptyState.locator("img.empty-state-illustration");
  await expect(illustration).toBeVisible();
  await expect(illustration).toHaveAttribute("src", "/illustrations/G7-empty-houses-light.svg");
  await expect(illustration).toHaveAttribute("alt", "");
  await expect(emptyState.locator(".empty-state-title")).toContainText("No houses");
});

test("cleaner portal empty state renders illustration with caption when no jobs", async ({ page }) => {
  const token = "00000000000000000000000000000001.0000000000000000000000000000000000000000000000000000000000000001";
  await page.route("**/api/cleaner-portal*", route => route.fulfill({json: {
    cleaner_name: "Test Cleaner",
    jobs: [],
    offers: [],
    preferred_language: null,
    reading_languages: ["en"],
    cleaner_version: 1,
    availability: {service_date: "2026-11-11", status: "pending", version: 1, can_change: true},
  }}));
  await page.goto(`/cleaner#${token}`);
  await page.waitForSelector(".cleaner-portal-section-heading");
  const emptyState = page.locator(".empty-state");
  await expect(emptyState).toBeVisible();
  const illustration = emptyState.locator("img.empty-state-illustration");
  await expect(illustration).toBeVisible();
  await expect(illustration).toHaveAttribute("src", "/illustrations/G9-portal-no-jobs-light.svg");
  await expect(illustration).toHaveAttribute("alt", "");
  await expect(emptyState.locator(".empty-state-title")).toContainText("all set for now");
});

test("onboarding success state renders G12 illustration after saving", async ({ page }) => {
  const token = "00000000000000000000000000000001.0000000000000000000000000000000000000000000000000000000000000001";
  await page.route("**/api/cleaner-onboarding*", async route => {
    if (route.request().method() === "GET") {
      return route.fulfill({json: {
        version: 1, cleaner_version: 1, cleaner_name: "Test Cleaner",
        reading_languages: ["en"], preferred_language: null,
        days: [], starting_address: "", route_status: null, routing_mode: "mock",
      }});
    } else {
      return route.fulfill({json: {
        version: 2, cleaner_version: 1, cleaner_name: "Test Cleaner",
        reading_languages: ["en"], preferred_language: "en",
        days: [], starting_address: "123 Test St", route_status: "ready", routing_mode: "mock",
      }});
    }
  });
  await page.goto(`/onboarding#${token}`);
  await page.waitForSelector(".weekly-hours form");
  await page.fill('input[name="address"]', "123 Test St, Charlotte, NC 28202");
  await page.click(".weekly-save-button");
  await expect(page.locator(".weekly-saved")).toBeVisible();
  const illustration = page.locator(".weekly-saved-illustration");
  await expect(illustration).toBeVisible();
  await expect(illustration).toHaveAttribute("src", "/illustrations/G12-success-light.svg");
  await expect(illustration).toHaveAttribute("alt", "");
});

test("onboarding page renders G10 welcome illustration", async ({ page }) => {
  const token = "00000000000000000000000000000001.0000000000000000000000000000000000000000000000000000000000000001";
  await page.route("**/api/cleaner-onboarding*", route => route.fulfill({json: {
    version: 1, cleaner_version: 1, cleaner_name: "Test Cleaner",
    reading_languages: ["en"], preferred_language: null,
    days: [], starting_address: "", route_status: null, routing_mode: "mock",
  }}));
  await page.goto(`/onboarding#${token}`);
  await page.waitForSelector(".weekly-hours");
  const illustration = page.locator(".onboarding-welcome-illustration");
  await expect(illustration).toBeVisible();
  await expect(illustration).toHaveAttribute("src", "/illustrations/G10-onboarding-welcome-light.svg");
  await expect(illustration).toHaveAttribute("alt", "");
});

test("signup page renders G11 welcome illustration", async ({ page }) => {
  const response = await page.goto("/signup");
  if (response?.status() === 404) {
    test.skip(true, "Signup page requires WORKSPACE_SIGNUP_ENABLED=true");
  }
  const illustration = page.locator(".workspace-welcome-illustration");
  await expect(illustration).toBeVisible();
  await expect(illustration).toHaveAttribute("src", "/illustrations/G11-manager-first-run-light.svg");
  await expect(illustration).toHaveAttribute("alt", "");
});
