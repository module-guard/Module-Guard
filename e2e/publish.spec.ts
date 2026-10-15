import { test, expect, type Page } from "@playwright/test";
import { makeZip } from "./zip";

// Runs against the e2e stack: fake GitHub login, real upload checks, real Checkov.
const MAIN = 'resource "null_resource" "example" {}\n';
const VARIABLES = 'variable "name" {\n  type = string\n}\n';
// Assembled at runtime so no credential-shaped string is committed.
const AWS_KEY = "AKIA" + "Z7QHB3K2" + "LMNOP4RT";
// The preflight runs the real secret scanner before answering.
const PREFLIGHT = { timeout: 60000 };
// SCREENSHOT_DIR=screenshots npx playwright test e2e/publish.spec.ts
async function shot(page: Page, name: string) {
  const directory = process.env.SCREENSHOT_DIR;
  if (!directory) return;
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.screenshot({ path: `${directory}/${name}.png`, fullPage: true });
}

async function login(page: Page) {
  await page.goto("/new");
  await page.getByRole("link", { name: "GitHub로 로그인" }).first().click();
  await expect(
    page.getByRole("button", { name: "계정 메뉴: dev-user" }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/new$/);
}

async function chooseZip(
  page: Page,
  name: string,
  files: Record<string, string>,
) {
  await page.getByRole("tab", { name: "zip 파일" }).click();
  await page.locator("#source-zip").setInputFiles({
    name,
    mimeType: "application/zip",
    buffer: makeZip(files),
  });
  await page.getByRole("button", { name: "확인", exact: true }).click();
}

test("upload a zip → review → publish → scanned and listed", async ({
  page,
}) => {
  test.setTimeout(120000);
  await login(page);
  await shot(page, "20-new-source");
  await chooseZip(page, "E2E Module.zip", {
    "e2e-module/main.tf": MAIN,
    "e2e-module/variables.tf": VARIABLES,
    "e2e-module/README.md": "# e2e\n",
    "e2e-module/scripts/setup.sh": "#!/bin/sh\n",
    "e2e-module/docs/diagram.png": "png",
  });
  const result = page.getByRole("region", { name: "확인 결과" });
  await expect(result).toContainText("4개 파일을 올립니다", PREFLIGHT);
  await result.getByText("제외된 파일 1개").click();
  await expect(result).toContainText("docs/diagram.png");
  await expect(page.getByLabel("이름")).toHaveValue("e2e-module");
  await page.getByLabel("설명 (선택)").fill("e2e upload");
  await shot(page, "21-new-review");
  await page.getByRole("button", { name: "올리기", exact: true }).click();

  await expect(page).toHaveURL(/\/scans\/[a-f0-9-]+/);
  await expect(page.getByText("@dev-user/e2e-module")).toBeVisible();
  await expect(page.getByText("공개로 등록됨")).toBeVisible({ timeout: 90000 });
  await expect(page.getByText("완료", { exact: true }).first()).toBeVisible();
  await shot(page, "22-new-registered");

  await page.goto("/new");
  const mine = page.getByRole("link", { name: /e2e-module/ });
  await expect(mine).toContainText("v1");
  await expect(mine).toContainText("완료");
  // The same name cannot be registered twice.
  await chooseZip(page, "e2e-module.zip", { "main.tf": MAIN });
  await expect(page.getByRole("region", { name: "확인 결과" })).toBeVisible(
    PREFLIGHT,
  );
  await page.getByRole("button", { name: "올리기", exact: true }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "이미" }),
  ).toBeVisible();
});

test("state files reject the whole upload with the reason", async ({
  page,
}) => {
  await login(page);
  await chooseZip(page, "with-state.zip", {
    "main.tf": MAIN,
    "terraform.tfstate": "{}",
  });
  const alert = page.getByRole("alert").filter({ hasText: "민감한 파일" });
  await expect(alert).toContainText("terraform.tfstate", PREFLIGHT);
  await expect(alert).toContainText("state 파일");
  await shot(page, "24-new-sensitive-rejected");
  await expect(page.getByRole("region", { name: "확인 결과" })).toHaveCount(0);
});

test("a path-traversal archive is refused", async ({ page }) => {
  await login(page);
  await chooseZip(page, "evil.zip", { "main.tf": MAIN, "../../evil.tf": MAIN });
  await expect(
    page.getByRole("alert").filter({ hasText: "상위 폴더" }),
  ).toBeVisible();
});

test("a credential refuses the upload and only four characters are shown", async ({
  page,
}) => {
  await login(page);
  await chooseZip(page, "leaky.zip", {
    "main.tf": `provider "aws" {\n  access_key = "${AWS_KEY}"\n}\n`,
  });
  const alert = page.getByRole("alert").filter({ hasText: "비밀값" });
  await expect(alert).toContainText("main.tf:2", PREFLIGHT);
  await expect(alert).toContainText("AKIA••••");
  await shot(page, "23-new-secret-rejected");
  expect(await page.content()).not.toContain(AWS_KEY);
  expect(await page.content()).not.toContain(AWS_KEY.slice(0, 8));
  await expect(page.getByRole("region", { name: "확인 결과" })).toHaveCount(0);
});

test("a private module's scan is hidden from everyone else", async ({
  page,
  browser,
}) => {
  test.setTimeout(120000);
  await login(page);
  await chooseZip(page, "private-module.zip", { "main.tf": MAIN });
  await expect(page.getByRole("region", { name: "확인 결과" })).toBeVisible(
    PREFLIGHT,
  );
  await page.getByLabel("비공개").check();
  await page.getByRole("button", { name: "올리기", exact: true }).click();
  await expect(page.getByText("비공개로 등록됨")).toBeVisible({
    timeout: 90000,
  });
  const url = page.url();

  const stranger = await (await browser.newContext()).newPage();
  await stranger.goto(url);
  await expect(
    stranger.getByRole("alert").filter({ hasText: /not found/i }),
  ).toBeVisible();
  await expect(stranger.getByText("private-module")).toHaveCount(0);
  await stranger.goto("/explore");
  await expect(stranger.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(stranger.getByText("private-module")).toHaveCount(0);
  await stranger.goto("/@dev-user");
  await expect(stranger.getByText("private-module")).toHaveCount(0);
  // The owner still sees it on their home.
  await page.goto("/home");
  await expect(page.getByText("private-module").first()).toBeVisible();
});

test("publishing requires login", async ({ page }) => {
  await page.goto("/new");
  await expect(
    page.getByText("모듈을 올리려면 로그인이 필요합니다"),
  ).toBeVisible();
  await expect(page.getByRole("tab", { name: "zip 파일" })).toHaveCount(0);
});

test("a failed attempt is shown only to its owner with retry and delete", async ({
  page,
}) => {
  await login(page);
  // A failed first scan cannot be forced through the UI, so the list is stubbed.
  let deleted = false;
  await page.route("**/api/me/repositories", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        deleted
          ? []
          : [
              {
                name: "broken-module",
                description: null,
                visibility: "PUBLIC",
                origin_type: "GITHUB",
                origin_url: "https://github.com/org/broken-module",
                created_at: new Date().toISOString(),
                version: "v1",
                latest_scan: null,
                state: "FAILED",
                failure: "Source snapshot could not be stored.",
              },
            ],
      ),
    }),
  );
  await page.route("**/api/repositories/broken-module", (route) => {
    expect(route.request().method()).toBe("DELETE");
    deleted = true;
    return route.fulfill({ status: 204 });
  });
  await page.reload();
  const row = page.getByRole("listitem").filter({ hasText: "broken-module" });
  await expect(row).toContainText("소스를 보관하지 못해");
  await row.getByRole("link", { name: "다시 시도" }).click();
  await expect(page.getByLabel("GitHub 저장소 주소")).toHaveValue(
    "https://github.com/org/broken-module",
  );
  await row.getByRole("button", { name: "삭제" }).click();
  await expect(page.getByText("broken-module")).toHaveCount(0);
});
