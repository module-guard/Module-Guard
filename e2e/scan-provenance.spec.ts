import { expect, test } from "@playwright/test";
import { provenanceScan } from "./fixtures/scan-provenance";
import { partialAnalysis } from "./fixtures/analysis-coverage";

for (const mobile of [false, true]) {
  test(`provenance keyboard, copy and partial-result UI (${mobile ? "mobile" : "desktop"})`, async ({
    page,
  }) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async (value: string) => {
            (window as unknown as { copied: string }).copied = value;
          },
        },
      });
    });
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      const json = path.includes("/scans/")
        ? { ...provenanceScan, partial_result: true, analysis: partialAnalysis }
        : path === "/api/me"
          ? { user: null, login_available: true }
          : { status: "ok", database: "connected", checkov: "available" };
      await route.fulfill({
        json,
        headers: {
          "access-control-allow-origin":
            route.request().headers().origin ?? "*",
          "access-control-allow-credentials": "true",
        },
      });
    });
    await page.goto(`/@fixture/coverage/scans/${provenanceScan.id}`);
    const region = page.getByRole("region", { name: "분석 환경·후처리 이력" });
    const summary = region.locator("summary");
    await expect(summary).toBeVisible();
    await expect(region.getByText("최초 검사 분석 환경")).not.toBeVisible();
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(region.getByText("최초 검사 분석 환경")).toBeVisible();
    await expect(
      region.getByText("최근 후처리", { exact: true }),
    ).toBeVisible();
    await page.keyboard.press("Tab");
    await expect(region.getByRole("button")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(region.getByRole("status")).toHaveText("복사됨");
    const copied = await page.evaluate(
      () => (window as unknown as { copied: string }).copied,
    );
    expect(JSON.parse(copied).postprocessing.history).toHaveLength(1);
    expect(copied).not.toContain("SECRET");
    await expect(
      page.getByRole("region", { name: "분석 범위 안내" }),
    ).toBeVisible();
    expect(
      await region.evaluate((el) => el.scrollWidth <= el.clientWidth),
    ).toBe(true);
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", {
        value: {
          writeText: async () => {
            throw new Error("denied");
          },
        },
      });
    });
    await region.getByRole("button").click();
    await expect(region.getByRole("status")).toContainText(
      "복사하지 못했습니다",
    );
    await summary.focus();
    await page.keyboard.press("Space");
    await expect(region.getByRole("button")).not.toBeVisible();
  });
}
