import { createHmac } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

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

function createMockDashboard(todayOverrides?: Partial<(typeof defaultToday)[0]>[]) {
  const today = defaultToday.map((task, i) => ({
    ...task,
    ...(todayOverrides?.[i] ?? {}),
  }));
  return {
    service_date: "2026-11-10",
    tomorrow_date: "2026-11-11",
    today,
    tomorrow: [] as typeof defaultToday,
    availability: {},
    assignment_counts: {},
    ack_counts: { acknowledged: 1, awaiting: 1 },
    completion_counts: { completed: 0, open: 2 },
    exceptions: [] as unknown[],
    sync_health: { status: "succeeded", stale: false, publication_blocked: false },
    sms_delivery_counts: {},
    pending_deadline_count: 0,
    uncovered_count: 0,
    late_count: 0,
    dispatch_enabled: false,
  };
}

const defaultToday = [
  {
    id: "task-1",
    property_id: "prop-a",
    property_name: "Test Property A",
    cleaner_name: "Cleaner One",
    planned_start: "2026-11-10T10:00:00-05:00",
    planned_end: "2026-11-10T12:00:00-05:00",
    acknowledged: false,
    status: "assigned",
    pending: false,
    deadline_at: "2026-11-10T21:00:00Z",
    assignment_id: "assignment-1",
    assignment_version: 1,
    assignment_status: "published",
    is_locked: false,
    lock_reason: null as string | null,
    locked_by: null as string | null,
    cleaner_id: "cleaner-1",
  },
  {
    id: "task-2",
    property_id: "prop-b",
    property_name: "Test Property B",
    cleaner_name: "Cleaner Two",
    planned_start: "2026-11-10T13:00:00-05:00",
    planned_end: "2026-11-10T15:00:00-05:00",
    acknowledged: true,
    status: "assigned",
    pending: false,
    deadline_at: "2026-11-10T21:00:00Z",
    assignment_id: "assignment-2",
    assignment_version: 1,
    assignment_status: "published",
    is_locked: true,
    lock_reason: "Manager locked" as string | null,
    locked_by: "synthetic-manager" as string | null,
    cleaner_id: "cleaner-2",
  },
];

const mockCleaners = {
  items: [
    { id: "cleaner-1", display_name: "Cleaner One", eligible_property_ids: ["prop-a", "prop-b"] },
    { id: "cleaner-2", display_name: "Cleaner Two", eligible_property_ids: ["prop-a", "prop-b"] },
    { id: "cleaner-3", display_name: "Cleaner Three", eligible_property_ids: ["prop-a", "prop-b"] },
  ],
};

const mockSchedule = {
  active_run: {
    id: "run-1",
    version: 1,
    service_date: "2026-11-10",
    status: "published",
    input_fingerprint: "fp-1",
    day_revision: 1,
    solver_status: "feasible",
    uncovered: [],
    candidate_plan: { placed: [] },
  },
};

test("manager can lock and unlock an assignment", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");

  let isLocked = false;
  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => {
    const data = createMockDashboard([{ is_locked: isLocked, lock_reason: isLocked ? "Test lock" : null }]);
    return route.fulfill({ json: data });
  });
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleaners }));
  await page.route("**/api/schedules?*", (route) => route.fulfill({ json: mockSchedule }));
  await page.route("**/api/assignments/*/lock", async (route) => {
    const body = route.request().postDataJSON() as { locked: boolean; reason: string };
    isLocked = body.locked;
    await route.fulfill({ json: { assignment: { id: "assignment-1", version: 2, is_locked: isLocked } } });
  });

  page.on("dialog", (dialog) => dialog.accept("Test lock reason"));

  await page.goto("/schedules");
  await expect(page.getByRole("heading", { name: "Schedules" })).toBeVisible();

  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Assignment Management" })).toBeVisible();

  const mgmtSection = page.getByRole("region", { name: "Assignment management" });
  const firstRow = mgmtSection.locator("tr").filter({ hasText: "Test Property A" });
  await expect(firstRow.getByRole("button", { name: "Lock" })).toBeVisible();

  await firstRow.getByRole("button", { name: "Lock" }).click();
  await expect(firstRow.getByRole("button", { name: "Unlock" })).toBeVisible();

  await firstRow.getByRole("button", { name: "Unlock" }).click();
  await expect(firstRow.getByRole("button", { name: "Lock" })).toBeVisible();
});

test("manager can reassign an assignment", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");

  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => route.fulfill({ json: createMockDashboard() }));
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleaners }));
  await page.route("**/api/schedules?*", (route) => route.fulfill({ json: mockSchedule }));

  let overrideCalled = false;
  let receivedStart = "";
  await page.route("**/api/assignments/*/override", async (route) => {
    const body = route.request().postDataJSON() as { cleaner_id: string; planned_start: string; reason: string };
    expect(body.cleaner_id).toBe("cleaner-3");
    expect(body.planned_start).toContain("2026-11-10");
    expect(body.planned_start).toMatch(/-05:00$/);
    receivedStart = body.planned_start;
    overrideCalled = true;
    await route.fulfill({ json: { assignment: { id: "assignment-1", version: 2, cleaner_id: "cleaner-3" }, sms_sent: false } });
  });

  page.on("dialog", (dialog) => dialog.accept("Reassignment reason"));

  await page.goto("/schedules");
  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Assignment Management" })).toBeVisible();

  const mgmtSection = page.getByRole("region", { name: "Assignment management" });
  const firstRow = mgmtSection.locator("tr").filter({ hasText: "Test Property A" });
  await firstRow.getByRole("button", { name: "Reassign" }).click();

  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByLabel("New cleaner").selectOption("cleaner-3");
  await page.getByLabel("Start time").fill("2026-11-10T11:00");
  await page.getByRole("dialog").getByRole("button", { name: "Reassign" }).click();

  expect(overrideCalled).toBeTruthy();
  expect(receivedStart).toMatch(/2026-11-10T11:00:00-05:00/);
});

test("locked assignment shows lock indicator", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");

  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => route.fulfill({ json: createMockDashboard() }));
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleaners }));
  await page.route("**/api/schedules?*", (route) => route.fulfill({ json: mockSchedule }));

  await page.goto("/schedules");
  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Assignment Management" })).toBeVisible();

  const mgmtSection = page.getByRole("region", { name: "Assignment management" });
  const lockedRow = mgmtSection.locator("tr").filter({ hasText: "Test Property B" });
  await expect(lockedRow.getByRole("button", { name: "Unlock" })).toBeVisible();

  const unlockedRow = mgmtSection.locator("tr").filter({ hasText: "Test Property A" });
  await expect(unlockedRow.getByRole("button", { name: "Lock" })).toBeVisible();
});

test("viewer cannot see lock/unlock/reassign buttons after loading schedule", async ({ page }) => {
  await signIn(page, devToken("synthetic-viewer", ["viewer"]), "viewer");

  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => route.fulfill({ json: createMockDashboard() }));
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleaners }));
  await page.route("**/api/schedules?*", (route) => route.fulfill({ json: mockSchedule }));

  await page.goto("/schedules");
  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Schedules" })).toBeVisible();

  await expect(page.getByRole("heading", { name: "Assignment Management" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Lock" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Unlock" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Reassign" })).toHaveCount(0);
});

test("lock failure shows error message with code", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");

  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => route.fulfill({ json: createMockDashboard() }));
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleaners }));
  await page.route("**/api/schedules?*", (route) => route.fulfill({ json: mockSchedule }));
  await page.route("**/api/assignments/*/lock", async (route) => {
    await route.fulfill({
      status: 409,
      json: { error: { code: "stale_version", message: "Assignment version is stale." } },
    });
  });

  page.on("dialog", (dialog) => dialog.accept("Test lock reason"));

  await page.goto("/schedules");
  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Assignment Management" })).toBeVisible();

  const mgmtSection = page.getByRole("region", { name: "Assignment management" });
  const firstRow = mgmtSection.locator("tr").filter({ hasText: "Test Property A" });
  await firstRow.getByRole("button", { name: "Lock" }).click();

  await expect(page.locator("p[role=alert]")).toContainText("stale_version");
  await expect(page.locator("p[role=alert]")).toContainText("Assignment version is stale");
});

test("override failure shows error message with code", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");

  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => route.fulfill({ json: createMockDashboard() }));
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleaners }));
  await page.route("**/api/schedules?*", (route) => route.fulfill({ json: mockSchedule }));
  await page.route("**/api/assignments/*/override", async (route) => {
    await route.fulfill({
      status: 422,
      json: { error: { code: "infeasible", message: "Override overlaps another assignment." } },
    });
  });

  page.on("dialog", (dialog) => dialog.accept("Reassignment reason"));

  await page.goto("/schedules");
  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Assignment Management" })).toBeVisible();

  const mgmtSection = page.getByRole("region", { name: "Assignment management" });
  const firstRow = mgmtSection.locator("tr").filter({ hasText: "Test Property A" });
  await firstRow.getByRole("button", { name: "Reassign" }).click();

  await page.getByLabel("New cleaner").selectOption("cleaner-3");
  await page.getByLabel("Start time").fill("2026-11-10T11:00");
  await page.getByRole("dialog").getByRole("button", { name: "Reassign" }).click();

  await expect(page.locator("p[role=alert]")).toContainText("infeasible");
  await expect(page.locator("p[role=alert]")).toContainText("Override overlaps another assignment");
});

test("reassign succeeds end-to-end with timezone-aware timestamp (November EST -05:00)", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");

  let dashboardCallCount = 0;
  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => {
    dashboardCallCount++;
    const data = createMockDashboard();
    if (dashboardCallCount > 1) {
      data.today[0].cleaner_id = "cleaner-3";
      data.today[0].cleaner_name = "Cleaner Three";
    }
    return route.fulfill({ json: data });
  });
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleaners }));
  await page.route("**/api/schedules?*", (route) => route.fulfill({ json: mockSchedule }));

  let receivedPayload: { cleaner_id: string; planned_start: string; reason: string } | null = null;
  await page.route("**/api/assignments/*/override", async (route) => {
    receivedPayload = route.request().postDataJSON() as typeof receivedPayload;
    await route.fulfill({ json: { assignment: { id: "assignment-1", version: 2, cleaner_id: "cleaner-3" }, sms_sent: false } });
  });

  page.on("dialog", (dialog) => dialog.accept("End-to-end test reason"));

  await page.goto("/schedules");
  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Assignment Management" })).toBeVisible();

  const mgmtSection = page.getByRole("region", { name: "Assignment management" });
  const firstRow = mgmtSection.locator("tr").filter({ hasText: "Test Property A" });
  await firstRow.getByRole("button", { name: "Reassign" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();

  await page.getByLabel("New cleaner").selectOption("cleaner-3");
  await page.getByLabel("Start time").fill("2026-11-10T14:30");
  await page.getByRole("dialog").getByRole("button", { name: "Reassign" }).click();

  await expect(page.locator("p[role=status]").first()).toContainText("Assignment reassigned");

  expect(receivedPayload).not.toBeNull();
  expect(receivedPayload!.cleaner_id).toBe("cleaner-3");
  expect(receivedPayload!.planned_start).toBe("2026-11-10T14:30:00-05:00");
  expect(receivedPayload!.reason).toBe("End-to-end test reason");
});

test("reassign uses EDT -04:00 offset for summer dates", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");

  const summerToday = [
    {
      ...defaultToday[0],
      planned_start: "2026-07-10T10:00:00-04:00",
      planned_end: "2026-07-10T12:00:00-04:00",
    },
  ];

  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => {
    const data = createMockDashboard();
    data.service_date = "2026-07-10";
    data.today = summerToday;
    return route.fulfill({ json: data });
  });
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleaners }));
  await page.route("**/api/schedules?*", (route) => {
    const summerSchedule = { ...mockSchedule, active_run: { ...mockSchedule.active_run, service_date: "2026-07-10" } };
    return route.fulfill({ json: summerSchedule });
  });

  let receivedPayload: { planned_start: string } | null = null;
  await page.route("**/api/assignments/*/override", async (route) => {
    receivedPayload = route.request().postDataJSON() as typeof receivedPayload;
    await route.fulfill({ json: { assignment: { id: "assignment-1", version: 2 }, sms_sent: false } });
  });

  page.on("dialog", (dialog) => dialog.accept("Summer test"));

  await page.goto("/schedules");
  await page.getByLabel("Schedule date").fill("2026-07-10");
  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Assignment Management" })).toBeVisible();

  const mgmtSection = page.getByRole("region", { name: "Assignment management" });
  const firstRow = mgmtSection.locator("tr").filter({ hasText: "Test Property A" });
  await firstRow.getByRole("button", { name: "Reassign" }).click();
  await page.getByLabel("Start time").fill("2026-07-10T11:00");
  await page.getByRole("dialog").getByRole("button", { name: "Reassign" }).click();

  expect(receivedPayload).not.toBeNull();
  expect(receivedPayload!.planned_start).toBe("2026-07-10T11:00:00-04:00");
});

test("day change clears reassign dialog, error and notice", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");

  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => route.fulfill({ json: createMockDashboard() }));
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleaners }));
  await page.route("**/api/schedules?*", (route) => route.fulfill({ json: mockSchedule }));
  let overrideAttempts = 0;
  await page.route("**/api/assignments/*/override", async (route) => {
    overrideAttempts += 1;
    if (overrideAttempts === 1) {
      await route.fulfill({ json: { assignment: { id: "assignment-1", version: 2 }, sms_sent: false } });
    } else {
      await route.fulfill({
        status: 422,
        json: { error: { code: "test_error", message: "Test error message for day change" } },
      });
    }
  });

  page.on("dialog", (dialog) => dialog.accept("Test reason"));

  await page.goto("/schedules");
  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Assignment Management" })).toBeVisible();

  const mgmtSection = page.getByRole("region", { name: "Assignment management" });
  const firstRow = mgmtSection.locator("tr").filter({ hasText: "Test Property A" });

  await firstRow.getByRole("button", { name: "Reassign" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByLabel("Start time").fill("2026-11-10T11:00");
  await page.getByRole("dialog").getByRole("button", { name: "Reassign" }).click();
  await expect(page.locator("p[role=status]")).toContainText("reassigned");
  expect(overrideAttempts).toBe(1);

  await firstRow.getByRole("button", { name: "Reassign" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByLabel("Start time").fill("2026-11-10T12:00");
  await page.getByRole("dialog").getByRole("button", { name: "Reassign" }).click();
  await expect(page.locator("p[role=alert]")).toContainText("Test error message");
  expect(overrideAttempts).toBe(2);

  await page.getByLabel("Schedule date").fill("2026-11-11");

  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator("p[role=alert]")).toHaveCount(0);
  await expect(page.locator("p[role=status]")).toHaveCount(0);
});

test("override locked assignment shows 409 locked error", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");

  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => route.fulfill({ json: createMockDashboard() }));
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleaners }));
  await page.route("**/api/schedules?*", (route) => route.fulfill({ json: mockSchedule }));
  await page.route("**/api/assignments/*/override", async (route) => {
    await route.fulfill({
      status: 409,
      json: { error: { code: "locked", message: "Unlock the assignment before overriding it." } },
    });
  });

  page.on("dialog", (dialog) => dialog.accept("Test"));

  await page.goto("/schedules");
  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Assignment Management" })).toBeVisible();

  const mgmtSection = page.getByRole("region", { name: "Assignment management" });
  const firstRow = mgmtSection.locator("tr").filter({ hasText: "Test Property A" });
  await firstRow.getByRole("button", { name: "Reassign" }).click();
  await page.getByLabel("Start time").fill("2026-11-10T11:00");
  await page.getByRole("dialog").getByRole("button", { name: "Reassign" }).click();

  await expect(page.locator("p[role=alert]")).toContainText("locked");
  await expect(page.locator("p[role=alert]")).toContainText("Unlock the assignment");
});

test("non-JSON error body shows HTTP status", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");

  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => route.fulfill({ json: createMockDashboard() }));
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleaners }));
  await page.route("**/api/schedules?*", (route) => route.fulfill({ json: mockSchedule }));
  await page.route("**/api/assignments/*/lock", async (route) => {
    await route.fulfill({
      status: 502,
      body: "Bad Gateway",
      contentType: "text/plain",
    });
  });

  page.on("dialog", (dialog) => dialog.accept("Test"));

  await page.goto("/schedules");
  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Assignment Management" })).toBeVisible();

  const mgmtSection = page.getByRole("region", { name: "Assignment management" });
  const firstRow = mgmtSection.locator("tr").filter({ hasText: "Test Property A" });
  await firstRow.getByRole("button", { name: "Lock" }).click();

  await expect(page.locator("p[role=alert]")).toContainText("502");
  await expect(page.locator("p[role=alert]")).toContainText("Bad Gateway");
});

test("reassign dropdown filters cleaners by property eligibility", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");

  const mockCleanersWithEligibility = {
    items: [
      { id: "cleaner-1", display_name: "Cleaner One", eligible_property_ids: ["prop-a", "prop-b"] },
      { id: "cleaner-2", display_name: "Cleaner Two", eligible_property_ids: ["prop-b"] },
      { id: "cleaner-3", display_name: "Cleaner Three", eligible_property_ids: ["prop-a"] },
    ],
  };

  const todayWithPropertyId = [
    { ...defaultToday[0], property_id: "prop-a" },
  ];

  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => {
    const data = createMockDashboard();
    data.today = todayWithPropertyId;
    return route.fulfill({ json: data });
  });
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleanersWithEligibility }));
  await page.route("**/api/schedules?*", (route) => route.fulfill({ json: mockSchedule }));

  await page.goto("/schedules");
  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Assignment Management" })).toBeVisible();

  const mgmtSection = page.getByRole("region", { name: "Assignment management" });
  const firstRow = mgmtSection.locator("tr").filter({ hasText: "Test Property A" });
  await firstRow.getByRole("button", { name: "Reassign" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();

  const options = await page.getByLabel("New cleaner").locator("option").allTextContents();
  expect(options).toContain("Cleaner One");
  expect(options).toContain("Cleaner Three");
  expect(options).not.toContain("Cleaner Two");
});

test("Eastern-vs-UTC list filter shows assignments on correct business date", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");

  // Late evening assignment in Eastern time (2026-11-10 at 9pm ET = 2026-11-11 02:00 UTC)
  // This should appear on November 10 since the business date uses Eastern time
  const lateEveningAssignment = {
    ...defaultToday[0],
    id: "task-late",
    property_name: "Late Property",
    planned_start: "2026-11-11T02:00:00Z", // Nov 10 at 9pm ET
    planned_end: "2026-11-11T04:00:00Z", // Nov 10 at 11pm ET
    assignment_id: "assignment-late",
    assignment_version: 1,
    assignment_status: "published",
    is_locked: false,
    cleaner_id: "cleaner-1",
    cleaner_name: "Late Night Cleaner",
    property_id: "prop-late",
  };

  // Early morning assignment in Eastern time (2026-11-11 at 1am ET = 2026-11-11 06:00 UTC)
  // This should NOT appear on November 10 since it's after midnight Eastern
  const earlyMorningAssignment = {
    ...defaultToday[0],
    id: "task-early",
    property_name: "Early Property",
    planned_start: "2026-11-11T06:00:00Z", // Nov 11 at 1am ET
    planned_end: "2026-11-11T08:00:00Z", // Nov 11 at 3am ET
    assignment_id: "assignment-early",
    assignment_version: 1,
    assignment_status: "published",
    is_locked: false,
    cleaner_id: "cleaner-2",
    cleaner_name: "Early Bird Cleaner",
    property_id: "prop-early",
  };

  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => {
    const data = createMockDashboard();
    data.today = [lateEveningAssignment, earlyMorningAssignment];
    return route.fulfill({ json: data });
  });
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleaners }));
  await page.route("**/api/schedules?*", (route) => route.fulfill({ json: mockSchedule }));

  await page.goto("/schedules");
  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Assignment Management" })).toBeVisible();

  const mgmtSection = page.getByRole("region", { name: "Assignment management" });

  // Late evening assignment should appear for Nov 10 (9pm ET is still Nov 10)
  await expect(mgmtSection.locator("tr").filter({ hasText: "Late Property" })).toBeVisible();

  // Early morning assignment should NOT appear for Nov 10 (1am ET is Nov 11)
  await expect(mgmtSection.locator("tr").filter({ hasText: "Early Property" })).toHaveCount(0);
});

test("completed and canceled assignments are filtered from management list", async ({ page }) => {
  await signIn(page, devToken("synthetic-manager", ["manager"]), "manager");

  const completedAssignment = {
    ...defaultToday[0],
    id: "task-completed",
    property_name: "Completed Property",
    assignment_id: "assignment-completed",
    assignment_status: "completed",
    property_id: "prop-completed",
  };

  const canceledAssignment = {
    ...defaultToday[0],
    id: "task-canceled",
    property_name: "Canceled Property",
    assignment_id: "assignment-canceled",
    assignment_status: "canceled",
    property_id: "prop-canceled",
  };

  const publishedAssignment = {
    ...defaultToday[0],
    id: "task-published",
    property_name: "Published Property",
    assignment_id: "assignment-published",
    assignment_status: "published",
    property_id: "prop-published",
  };

  await page.route("**/api/demo/account", (route) =>
    route.fulfill({
      json: { account_name: "Test", enabled: false, busy: false, last_run_at: null, last_error: null, last_outcome: "Paused", scope: "test" },
    }),
  );
  await page.route("**/api/dashboard*", (route) => {
    const data = createMockDashboard();
    data.today = [completedAssignment, canceledAssignment, publishedAssignment];
    return route.fulfill({ json: data });
  });
  await page.route("**/api/cleaners", (route) => route.fulfill({ json: mockCleaners }));
  await page.route("**/api/schedules?*", (route) => route.fulfill({ json: mockSchedule }));

  await page.goto("/schedules");
  await page.getByRole("button", { name: "Load schedule" }).click();
  await expect(page.getByRole("heading", { name: "Assignment Management" })).toBeVisible();

  const mgmtSection = page.getByRole("region", { name: "Assignment management" });

  // Published assignment should appear
  await expect(mgmtSection.locator("tr").filter({ hasText: "Published Property" })).toBeVisible();

  // Completed assignment should NOT appear
  await expect(mgmtSection.locator("tr").filter({ hasText: "Completed Property" })).toHaveCount(0);

  // Canceled assignment should NOT appear
  await expect(mgmtSection.locator("tr").filter({ hasText: "Canceled Property" })).toHaveCount(0);
});
