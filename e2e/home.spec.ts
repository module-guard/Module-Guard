import { test, expect } from "@playwright/test";
import { makeZip } from "./zip";

const PREFLIGHT = { timeout: 60000 };

test("home: my modules with badges, what needs a look and recent activity", async ({
  page,
}) => {
  test.setTimeout(180000);
  await page.goto("/home");
  await page.getByRole("link", { name: "GitHub로 로그인" }).last().click();
  // Login started from /home comes back to it.
  await expect(page).toHaveURL(/\/home$/);
  await expect(page.getByRole("heading", { name: "내 홈" })).toBeVisible();

  await page.goto("/new");
  await page.getByRole("tab", { name: "zip 파일" }).click();
  await page.locator("#source-zip").setInputFiles({
    name: "home-module.zip",
    mimeType: "application/zip",
    buffer: makeZip({ "main.tf": 'resource "null_resource" "x" {}\n' }),
  });
  await page.getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.getByRole("region", { name: "확인 결과" })).toBeVisible(
    PREFLIGHT,
  );
  await page.getByRole("button", { name: "올리기", exact: true }).click();
  await expect(page.getByText("공개로 등록됨")).toBeVisible({
    timeout: 120000,
  });

  await page.goto("/home");
  const mine = page.getByRole("region", { name: "내 모듈" });
  const card = mine.locator('a[href="/@dev-user/home-module"]');
  await expect(card).toBeVisible();
  await expect(card).toContainText("주요 위험 없음");
  await expect(
    page
      .getByRole("region", { name: "최근 활동" })
      .getByText("home-module")
      .first(),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "위험도 분포" })).toContainText(
    "본체 탐지",
  );
  // Logo and account menu lead home when signed in.
  await page.getByRole("button", { name: "계정 메뉴: dev-user" }).click();
  await expect(page.getByRole("menuitem", { name: "내 홈" })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.goto("/explore");
  await page.getByRole("link", { name: "ModuleGuard home" }).click();
  await expect(page).toHaveURL(/\/home$/);
  if (process.env.SCREENSHOT_DIR) {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.waitForTimeout(600);
    await page.screenshot({
      path: `${process.env.SCREENSHOT_DIR}/44-home.png`,
      fullPage: true,
    });
  }
});
