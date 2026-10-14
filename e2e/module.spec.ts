import { test, expect, type Page } from "@playwright/test";
import { makeZip } from "./zip";

const MAIN = 'resource "null_resource" "example" {}\n';
const VARIABLES =
  'variable "name" {\n  description = "Module name"\n  type        = string\n}\n' +
  'variable "enabled" {\n  type    = bool\n  default = true\n}\n' +
  'output "id" {\n  description = "The id"\n  value       = "x"\n}\n';
// Everything a hostile README might try. None of it may execute or become an element.
const HOSTILE_README = [
  "# Module Title",
  "",
  "Plain **bold** text.",
  "",
  "<script>window.__xss = 'script'</script>",
  "",
  "<img src=x onerror=\"window.__xss='onerror'\">",
  "",
  "[click me](javascript:window.__xss='link')",
  "",
  "![tracker](http://example.com/pixel.png)",
  "",
  "![inline](data:image/svg+xml,<svg onload=\"window.__xss='svg'\">)",
  "",
  "[safe link](https://example.com/docs)",
  "",
  '<iframe src="https://example.com"></iframe>',
].join("\n");
const PREFLIGHT = { timeout: 60000 };
async function shot(page: Page, name: string) {
  const directory = process.env.SCREENSHOT_DIR;
  if (!directory) return;
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${directory}/${name}.png`, fullPage: true });
}

async function login(page: Page, path = "/new") {
  await page.goto(path);
  await page.getByRole("link", { name: "GitHub로 로그인" }).first().click();
  await expect(
    page.getByRole("button", { name: "계정 메뉴: dev-user" }),
  ).toBeVisible();
}

async function upload(page: Page, name: string, files: Record<string, string>) {
  await page.getByRole("tab", { name: "zip 파일" }).click();
  await page.locator("#source-zip").setInputFiles({
    name,
    mimeType: "application/zip",
    buffer: makeZip(files),
  });
  await page.getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.getByRole("region", { name: "확인 결과" })).toBeVisible(
    PREFLIGHT,
  );
}

test("module page: README, interface, security badge, versions and a new version", async ({
  page,
}) => {
  test.setTimeout(240000);
  await login(page);
  await upload(page, "page-module.zip", {
    "main.tf": MAIN,
    "variables.tf": VARIABLES,
    "README.md": HOSTILE_README,
    LICENSE: "Apache License\nVersion 2.0, January 2004\n",
  });
  await page.getByLabel("설명 (선택)").fill("Module page e2e");
  await page.getByRole("button", { name: "올리기", exact: true }).click();
  await expect(page).toHaveURL(/\/@dev-user\/page-module\/scans\/[a-f0-9-]+/);
  await expect(page.getByText("공개로 등록됨")).toBeVisible({
    timeout: 120000,
  });

  // The scan page links back to the module page.
  await page.getByRole("link", { name: "@dev-user/page-module" }).click();
  await expect(page).toHaveURL(/\/@dev-user\/page-module$/);
  await expect(page.getByRole("heading", { level: 1 }).first()).toContainText(
    "@dev-user / page-module",
  );
  await expect(page.getByText("Module page e2e")).toBeVisible();
  await expect(page.getByText("주요 위험 없음").first()).toBeVisible();
  await expect(page.getByText(/검사: v1 ·/).first()).toBeVisible();
  await expect(page.getByText("Apache-2.0")).toBeVisible();

  // README is rendered, but nothing hostile ran or became an element.
  const readme = page.locator(".markdown");
  await expect(
    readme.getByRole("heading", { name: "Module Title" }),
  ).toBeVisible();
  await expect(readme.locator("strong")).toHaveText("bold");
  expect(
    await page.evaluate(() => (window as never as { __xss?: string }).__xss),
  ).toBeUndefined();
  await expect(readme.locator("script, iframe, img, svg")).toHaveCount(0);
  const links = await readme
    .locator("a")
    .evaluateAll((nodes) =>
      nodes.map((a) => [
        a.getAttribute("href"),
        a.getAttribute("rel"),
        a.getAttribute("target"),
      ]),
    );
  expect(links).toEqual([
    ["https://example.com/docs", "noopener noreferrer nofollow", "_blank"],
  ]);
  expect(await readme.innerHTML()).not.toMatch(/onerror=|onload=|javascript:/i);
  await expect(readme).toContainText("click me");
  await shot(page, "25-module-overview");

  await page.getByRole("link", { name: "입력·출력" }).click();
  await expect(page).toHaveURL(/tab=interface/);
  const name = page.getByRole("row").filter({ hasText: "Module name" });
  await expect(name).toContainText("필수");
  await expect(name).toContainText("string");
  await expect(
    page.getByRole("row").filter({ hasText: "enabled" }),
  ).toContainText("true");
  await expect(
    page.getByRole("row").filter({ hasText: "The id" }),
  ).toBeVisible();
  await shot(page, "26-module-interface");

  await page.getByRole("link", { name: "보안", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: /위험도 분포/ }),
  ).toBeVisible();
  await shot(page, "27-module-security");
  await page.getByRole("link", { name: "상세 보기 →" }).click();
  await expect(page).toHaveURL(/\/@dev-user\/page-module\/scans\//);
  await page
    .getByRole("main")
    .getByRole("link", { name: "모듈", exact: true })
    .click();
  await expect(page).toHaveURL(/\/@dev-user\/page-module$/);

  // Upload a second version of the same module.
  await page.getByRole("link", { name: "새 버전 올리기" }).click();
  await expect(
    page.getByRole("heading", { name: "새 버전 올리기" }),
  ).toBeVisible();
  await upload(page, "anything.zip", {
    "main.tf": MAIN,
    "README.md": "# Second version\n",
  });
  await expect(page.getByLabel("이름")).toHaveCount(0);
  await page.getByRole("button", { name: "새 버전으로 올리기" }).click();
  await expect(page).toHaveURL(/\/@dev-user\/page-module\/scans\//);
  await expect(page.getByText("v2 · 공개로 등록됨")).toBeVisible({
    timeout: 120000,
  });

  await page.goto("/@dev-user/page-module?tab=versions");
  const rows = page.getByRole("listitem");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("v2");
  await expect(rows.first()).toContainText("최신");
  await expect(rows.nth(1)).toContainText("v1");
  await page.waitForTimeout(600); // let the page fade-in finish before the picture
  await shot(page, "28-module-versions");
  await page.goto("/@dev-user/page-module");
  await expect(page.locator(".markdown")).toContainText("Second version");
  // The first version stays readable.
  await page.goto("/@dev-user/page-module?version=v1");
  await expect(page.locator(".markdown")).toContainText("Module Title");
  await expect(page.getByText("v1을 보고 있습니다.")).toBeVisible();
});

test("a private module page is hidden from everyone else", async ({
  page,
  browser,
}) => {
  test.setTimeout(180000);
  await login(page);
  await upload(page, "secret-page.zip", { "main.tf": MAIN });
  await page.getByLabel("비공개").check();
  await page.getByRole("button", { name: "올리기", exact: true }).click();
  await expect(page.getByText("비공개로 등록됨")).toBeVisible({
    timeout: 120000,
  });
  await page.goto("/@dev-user/secret-page");
  await expect(page.getByText("비공개").first()).toBeVisible();

  const stranger = await (await browser.newContext()).newPage();
  await stranger.goto("/@dev-user/secret-page");
  await expect(stranger.getByText("모듈을 찾을 수 없습니다")).toBeVisible();
  await expect(stranger.getByText("secret-page")).toHaveCount(0);
});

test("addresses that are not /@owner/name are not module pages", async ({
  page,
}) => {
  // Without the @, the address is not a module page at all: a real HTTP 404.
  for (const path of ["/nobody/thing", "/nope", "/a/b/c/d"]) {
    const response = await page.goto(path);
    expect(response?.status(), path).toBe(404);
  }
  await expect(
    page.getByRole("heading", { name: "페이지를 찾을 수 없습니다" }),
  ).toBeVisible();
  await page.goto("/@nobody/thing");
  await expect(page.getByText("모듈을 찾을 수 없습니다")).toBeVisible();
});

test("findings in examples are kept apart from the module body", async ({
  page,
}) => {
  test.setTimeout(180000);
  const bucket = (name: string) =>
    `resource "aws_s3_bucket" "${name}" {\n  bucket = "${name}-bucket"\n}\n`;
  const versions =
    'terraform {\n  required_providers {\n    aws = { source = "hashicorp/aws" }\n' +
    '    random = { source = "hashicorp/random" }\n  }\n}\n';
  await login(page);
  await upload(page, "scoped-module.zip", {
    "main.tf": bucket("body"),
    "versions.tf": versions,
    "examples/basic/main.tf": bucket("demo_a") + bucket("demo_b"),
    "tests/unit/main.tf": bucket("fixture"),
  });
  await page.getByRole("button", { name: "올리기", exact: true }).click();
  await expect(page.getByText("공개로 등록됨")).toBeVisible({
    timeout: 120000,
  });
  const api = process.env.PLAYWRIGHT_API_URL || "http://localhost:8000";
  const detail = await (
    await page.request.get(`${api}/api/repositories/dev-user/scoped-module`)
  ).json();
  const scan = detail.version.scan;
  // One bucket in the body, three under examples/ and tests/: each High rule fires once
  // in the body and three times outside it.
  expect(scan.summary.high).toBeGreaterThan(0);
  expect(scan.scope_summary.example.high).toBe(scan.summary.high * 3);
  expect(scan.scope_summary.example.total).toBeGreaterThan(scan.summary.total);
  expect(detail.provider).toBe("aws");

  await page.goto("/@dev-user/scoped-module?tab=security");
  // The badge counts kinds of High rules in the body only.
  await expect(
    page.getByText(`주의 ${detail.version.high_rules}종`).first(),
  ).toBeVisible();
  await expect(
    page.getByText(`탐지 ${scan.summary.total}`, { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText(`예제에서 ${scan.scope_summary.example.total}건`),
  ).toBeVisible();
  await expect(page.getByText("aws", { exact: true })).toBeVisible();
  await shot(page, "32-module-security-examples");

  await page.getByRole("link", { name: "상세 보기 →" }).click();
  await page.getByRole("button", { name: /^보안 설정/ }).click();
  const cards = page.locator("article");
  await expect(cards.first()).toBeVisible();
  await expect(cards.filter({ hasText: "examples/" })).toHaveCount(0);
  await expect(
    page.locator("article").getByText("예제", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: /^예제에서 \d+건$/ }).click();
  await expect(cards.first().getByText("예제", { exact: true })).toBeVisible();
  await expect(cards.first()).toContainText(/examples|tests/);
});

test("owner settings: visibility, rename, description and delete by retyping the name", async ({
  page,
  browser,
}) => {
  test.setTimeout(180000);
  await login(page);
  await upload(page, "settings-module.zip", { "main.tf": MAIN });
  await page.getByRole("button", { name: "올리기", exact: true }).click();
  await expect(page.getByText("공개로 등록됨")).toBeVisible({
    timeout: 120000,
  });
  const stranger = await (await browser.newContext()).newPage();
  await stranger.goto("/@dev-user/settings-module?tab=settings");
  // The tab and its contents exist only for the owner.
  await expect(stranger.getByRole("heading", { level: 1 })).toContainText(
    "settings-module",
  );
  await expect(stranger.getByRole("link", { name: "설정" })).toHaveCount(0);
  await expect(stranger.getByText("모듈 삭제")).toHaveCount(0);

  await page.goto("/@dev-user/settings-module");
  await page
    .getByRole("navigation", { name: "모듈 탭" })
    .getByRole("link", { name: "설정" })
    .click();
  await shot(page, "37-module-settings");

  // Public → private: everyone else loses the page.
  await page.getByRole("button", { name: "비공개로 바꾸기" }).click();
  await expect(
    page.getByRole("button", { name: "공개로 바꾸기" }),
  ).toBeVisible();
  await stranger.reload();
  await expect(stranger.getByText("모듈을 찾을 수 없습니다")).toBeVisible();
  await page.getByRole("button", { name: "공개로 바꾸기" }).click();
  await expect(
    page.getByRole("button", { name: "비공개로 바꾸기" }),
  ).toBeVisible();

  // Rename + description: the address moves, the old one is gone.
  const save = page.getByRole("button", { name: "저장", exact: true });
  await expect(save).toBeDisabled();
  await page.getByLabel("이름", { exact: true }).fill("Bad Name");
  await expect(save).toBeDisabled();
  await page.getByLabel("이름", { exact: true }).fill("settings-renamed");
  await page.getByLabel("설명", { exact: true }).fill("Renamed in settings");
  await save.click();
  await expect(page).toHaveURL(/\/@dev-user\/settings-renamed\?tab=settings$/);
  await expect(page.getByRole("heading", { level: 1 }).first()).toContainText(
    "@dev-user / settings-renamed",
  );
  await expect(page.getByText("Renamed in settings")).toBeVisible();
  await stranger.goto("/@dev-user/settings-module");
  await expect(stranger.getByText("모듈을 찾을 수 없습니다")).toBeVisible();
  await stranger.goto("/@dev-user/settings-renamed");
  await expect(stranger.getByText("Renamed in settings")).toBeVisible();

  // Delete stays disabled until the name is typed exactly.
  await page.getByRole("button", { name: "이 모듈 삭제" }).click();
  const confirm = page.getByRole("button", { name: "영구 삭제" });
  await expect(confirm).toBeDisabled();
  await page.getByLabel("모듈 이름 확인").fill("settings-module");
  await expect(confirm).toBeDisabled();
  await shot(page, "38-module-delete-confirm");
  await page.getByLabel("모듈 이름 확인").fill("settings-renamed");
  await confirm.click();
  await expect(page).toHaveURL(/\/@dev-user$/);
  await expect(page.getByText("settings-renamed")).toHaveCount(0);
  await stranger.reload();
  await expect(stranger.getByText("모듈을 찾을 수 없습니다")).toBeVisible();
});
