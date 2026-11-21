import { expect, test } from "@playwright/test";

test("development shell renders without external requests", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (!url.startsWith(`http://127.0.0.1:${process.env.PLAYWRIGHT_PORT ?? "3000"}`)) {
      external.push(url);
    }
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Cleaning coordination" })).toBeVisible();
  await expect(page.getByText("Mock integrations only. No live dispatch.")).toHaveCount(1);
  expect(external).toEqual([]);
});

test("Inter font is served without authentication", async ({ request }) => {
  const response = await request.get("/fonts/Inter-Variable.woff2");
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toMatch(/font|octet-stream/);
});
