import { expect, test } from "@playwright/test";

test.describe("local demo", () => {
  test.skip(process.env.LOCAL_DEMO !== "1", "Dedicated isolated demo server required");
  test("manager tours the dashboard, publishes tomorrow and records mock SMS", async ({ page }) => {
    await page.goto("/");
    await Promise.all([page.waitForEvent("load"), page.getByRole("button", { name: "Connect to Hospitable" }).click()]);
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByLabel("Sign in as")).toHaveCount(0);
    await page.waitForSelector("section.today-cleaning-board table", {timeout: 10000});
    await page.locator("section.today-cleaning-board").getByRole("button", {name: "All"}).click();
    await expect(page.locator("section.today-cleaning-board").getByText("Willow Cottage (synthetic)", {exact: false})).toBeVisible({timeout: 10000});
    await expect(page.getByText("Confirmed").first()).toBeVisible();
    await page.screenshot({ path: "test-results/demo-dashboard.png", fullPage: true });
    await page.getByRole("link", { name: "Review assignments" }).click();
    const [loadedSchedule] = await Promise.all([
      page.waitForResponse(response => response.url().includes("/api/schedules?")),
      page.getByRole("button", { name: "Load schedule" }).click(),
    ]);
    expect(loadedSchedule.ok()).toBeTruthy();
    await expect(page.getByRole("region", { name: "Schedule plan" })).toBeVisible();
    // Confirm is a domain command, not a delivery callback.
    await page.getByRole("button", { name: "CONFIRM", exact: true }).and(page.locator(":enabled")).first().click();
    await expect(page.getByRole("status")).toContainText("CONFIRM processed");
    await page.getByLabel("Schedule date").fill("2026-11-11");
    await page.getByRole("button", { name: "Generate preview" }).click();
    await expect(page.getByRole("region", { name: "Schedule plan" })).toContainText("assignments");
    await page.getByRole("button", { name: "Publish validated schedule" }).click();
    await expect(page.getByRole("status")).toContainText("Schedule published");
    await page.getByRole("button", { name: "Record mock schedule messages" }).click();
    await expect(page.getByRole("status")).toContainText("No external send");
    await page.screenshot({ path: "test-results/demo-schedule.png", fullPage: true });
    // Permission coverage uses the test session API; role selection is not a manager task.
    expect((await page.request.post("/session", {data: {role: "viewer"}})).ok()).toBeTruthy();
    await page.reload();
    await expect(page.getByRole("button", { name: "Generate preview" })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Cleaner simulator" })).toHaveCount(0);
  });
});

test.describe("manager setup and proxy account", () => {
  test.skip(process.env.LOCAL_DEMO !== "1", "Dedicated isolated demo server required");
  test("manager adds a house and cleaner, sets hours, and supervises automation", async ({page}) => {
    await page.goto("/");
    await Promise.all([page.waitForEvent("load"), page.getByRole("button", {name: "Connect to Hospitable"}).click()]);
    await page.goto("/setup");
    await expect(page.getByRole("heading", {name: "Set up your operation"})).toBeVisible();
    await page.screenshot({path: "test-results/demo-setup.png", fullPage: true});
    await page.getByRole("link", {name: "Hospitable", exact: true}).click();
    await page.locator("summary:has-text('Connection details')").click();
    await page.locator("summary:has-text('Demo tools')").click();
    await page.getByLabel("Reservation house").selectOption({index: 1});
    await page.getByLabel("Checkout date").fill("2026-11-15");
    await page.getByLabel("Checkout time").fill("10:00");
    await page.getByLabel("Next arrival time").fill("16:00");
    await page.getByRole("button", {name: "Add synthetic bookings"}).click();
    await expect(page.getByRole("status")).toContainText("Synthetic");
    await page.getByRole("navigation", {name: "Primary"}).getByRole("link", {name: "Cleaners", exact: true}).click();
    const cleanerForm = page.locator("section[aria-label='Add a cleaner']");
    const cleanerName = `Synthetic setup cleaner ${Date.now()}`;
    await cleanerForm.getByLabel("Cleaner name").fill(cleanerName);
    await cleanerForm.getByLabel("Phone number").fill(`+1555555${String(Date.now() % 10000).padStart(4, "0")}`);
    await cleanerForm.getByRole("button", {name: "Add cleaner", exact: true}).click();
    await expect(page.getByRole("status")).toContainText("Cleaner added");
    await page.getByRole("combobox", {name: "Cleaner"}).selectOption({label: cleanerName});
    await page.locator("summary:has-text('Date-specific hours')").click();
    await page.getByLabel("Working date").fill("2026-11-11");
    await page.getByRole("button", {name: "Load working day"}).click();
    await page.getByLabel("Start time (Eastern)", {exact: true}).fill("09:00");
    await page.getByLabel("End time (Eastern)", {exact: true}).fill("17:00");
    await page.getByLabel("Synthetic starting point").fill("synthetic:harbor-origin");
    await page.getByRole("button", {name: "Save working day"}).click();
    await expect(page.getByRole("status")).toContainText("Date-specific hours saved");
    await page.getByRole("navigation", {name: "Primary"}).getByRole("link", {name: "Hospitable", exact: true}).click();
    await expect(page.getByRole("heading", {name: "Daily operations", exact: true})).toBeVisible();
    await page.locator("summary:has-text('Connection details')").click();
    await expect(page.getByText("Fictional reservations in the isolated demo", {exact: false})).toBeVisible();
    await page.getByRole("button", {name: "Start demo automation"}).click();
    const automation = page.locator("section[aria-label='Automation status']");
    await expect(automation.getByText("Last completed cycle:", {exact: false})).toBeVisible({timeout: 15000});
    await page.getByRole("button", {name: "Pause demo automation"}).click();
    await expect(page.getByRole("button", {name: "Start demo automation"})).toBeVisible();
    await page.getByRole("navigation", {name: "Primary"}).getByRole("link", {name: "Dashboard", exact: true}).click();
    await expect(page.getByText("Auto-refresh 30s", {exact: false})).toBeVisible();
    await page.screenshot({path: "test-results/demo-automation.png", fullPage: true});
    // Permission coverage uses the test session API; role selection is not a manager task.
    expect((await page.request.post("/session", {data: {role: "viewer"}})).ok()).toBeTruthy();
    await page.reload();
    await expect(page.getByRole("button", {name: "Start demo automation"})).toHaveCount(0);
    await page.goto("/properties");
    await expect(page.getByRole("button", {name: "Add house", exact: true})).toHaveCount(0);
  });
});


test.describe("one-click simulated connection", () => {
  test.skip(process.env.LOCAL_DEMO !== "1", "Dedicated isolated demo server required");
  test("connection can retry a failure and opens the manager dashboard", async ({page}) => {
    await page.route("**/session", route => route.fulfill({status: 503, json: {error: {message: "Synthetic outage"}}}));
    await page.goto("/");
    await expect(page.getByLabel("Sign in as")).toHaveCount(0);
    await expect(page.getByText("Explore the demo", {exact: true})).toHaveCount(0);
    const connect = page.getByRole("button", {name: "Connect to Hospitable", exact: true});
    await connect.click();
    await expect(page.getByRole("alert").filter({hasText: "Could not connect to the simulated account"})).toBeVisible();
    await expect(connect).toBeEnabled();
    await page.unroute("**/session");
    await connect.click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByLabel("Hospitable connection")).toContainText("simulated account");
    expect((await page.request.post("/session", {data: {connection: "hospitable", role: "owner"}})).ok()).toBeTruthy();
    const cookies = await page.context().cookies();
    expect(cookies.find(cookie => cookie.name === "cleaning-dev-role")?.value).toBe("manager");
    expect(cookies.find(cookie => cookie.name === "cleaning-dev-token")?.httpOnly).toBeTruthy();
    await expect(page.getByRole("button", {name: "Connect to Hospitable", exact: true})).toHaveCount(0);
  });
});
