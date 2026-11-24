import { expect, test } from "@playwright/test";

const token = `${"a".repeat(32)}.${"b".repeat(64)}`;
const week = {
  version: 1,
  cleaner_version: 1,
  reading_languages: ["en"],
  preferred_language: null,
  cleaner_name: "Alex",
  days: [{ weekday: 0, start_time: "08:00", end_time: "16:00" }],
};

for (const [language, width] of [["en", 320], ["es", 390], ["pt", 430]] as const) {
  test(`cleaner can edit and save onboarding on a ${width}px phone in ${language}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    await page.route("**/api/cleaner-onboarding", async route => {
      await route.fulfill({ json: route.request().method() === "GET" ? week : { ...week, version: 2 } });
    });
    await page.goto(`/onboarding?lang=${language}#${token}`);
    const form = page.locator(".cleaner-onboarding form");
    await expect(form).toBeVisible();
    await expect(page.locator("main")).toHaveAttribute("lang", language);

    const start = form.locator('[name="usual-start"]');
    const finish = form.locator('[name="usual-end"]');
    await expect(start).toHaveCSS("font-size", "16px");
    await start.fill("09:00");
    await finish.fill("17:00");
    const usualBreak = form.locator("summary").first();
    expect((await usualBreak.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    await usualBreak.click();
    await form.locator('[name="usual-break-start"]').fill("12:00");
    await form.locator('[name="usual-break-end"]').fill("12:30");

    await form.locator('[name="custom-hours"]').check();
    await form.locator('[name="start-0"]').fill("09:30");
    await form.locator('[name="end-0"]').fill("17:00");
    const customBreak = form.locator(".weekly-custom-hours summary").first();
    if (!(await form.locator('[name="break-start-0"]').isVisible())) await customBreak.click();
    await form.locator('[name="break-start-0"]').fill("12:00");
    await form.locator('[name="break-end-0"]').fill("12:30");
    await form.locator('[name="address"]').fill("123 Sample Lane, Charlotte, NC");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);

    const saved = page.waitForRequest(request =>
      request.url().endsWith("/api/cleaner-onboarding") && request.method() === "POST"
    );
    await form.getByRole("button").click();
    expect((await saved).postDataJSON()).toMatchObject({
      preferred_language: language,
      starting_address: "123 Sample Lane, Charlotte, NC",
      days: [{ weekday: 0, start_time: "09:30", end_time: "17:00", break_start: "12:00", break_end: "12:30" }],
    });
    await expect(page.locator(".weekly-saved")).toBeVisible();
  });
}
