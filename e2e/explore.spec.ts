import {
  test,
  expect,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import { makeZip } from "./zip";

// Demo scenario steps 1-4 and 7 with two users. The development fake provider signs in
// whoever the mg_dev_login cookie names.
const api = process.env.PLAYWRIGHT_API_URL || "http://localhost:8000";
const PREFLIGHT = { timeout: 60000 };
const MODULE = `explore-vpc-${Date.now().toString(36)}`;
const bucket =
  'resource "aws_s3_bucket" "logs" {\n  bucket = "explore-logs"\n}\n';
const versions =
  'terraform {\n  required_providers {\n    aws = { source = "hashicorp/aws" }\n  }\n}\n';

async function shot(page: Page, name: string) {
  const directory = process.env.SCREENSHOT_DIR;
  if (!directory) return;
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${directory}/${name}.png`, fullPage: true });
}

async function signedIn(browser: Browser, login: string) {
  const context = await browser.newContext();
  await context.addCookies([{ name: "mg_dev_login", value: login, url: api }]);
  const page = await context.newPage();
  await page.goto("/new");
  await page.getByRole("link", { name: "GitHub로 로그인" }).first().click();
  await expect(
    page.getByRole("button", { name: `계정 메뉴: ${login}` }),
  ).toBeVisible();
  return { context, page };
}

async function publish(page: Page) {
  await page.getByRole("tab", { name: "zip 파일" }).click();
  await page.locator("#source-zip").setInputFiles({
    name: `${MODULE}.zip`,
    mimeType: "application/zip",
    buffer: makeZip({
      "main.tf": bucket,
      "versions.tf": versions,
      "README.md": "# Explore VPC\n\nA network module for the explore test.\n",
      LICENSE: "Apache License\nVersion 2.0, January 2004\n",
    }),
  });
  await page.getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.getByRole("region", { name: "확인 결과" })).toBeVisible(
    PREFLIGHT,
  );
  await page.getByLabel("설명 (선택)").fill("Network for the explore test");
  await page.getByRole("button", { name: "올리기", exact: true }).click();
  await expect(page.getByText("공개로 등록됨")).toBeVisible({
    timeout: 120000,
  });
}

function write(context: BrowserContext) {
  // Cookie-authenticated writes need an allowed Origin, as the browser would send.
  const origin = new URL(test.info().project.use.baseURL!).origin;
  return { headers: { Origin: origin } };
}

test("find a module, star it, fork it, and see it on the profile", async ({
  browser,
  page,
}) => {
  test.setTimeout(300000);
  const alice = await signedIn(browser, "alice-e2e");
  await publish(alice.page);
  const detail = await (
    await alice.context.request.get(
      `${api}/api/repositories/alice-e2e/${MODULE}`,
    )
  ).json();
  const badge = `주의 ${detail.version.high_rules}종`;
  expect(detail.version.high_rules).toBeGreaterThan(0);

  // 1. Anonymous visitor searches the catalogue.
  await page.goto("/explore");
  await page.getByLabel("모듈 검색").fill(MODULE);
  await page.getByRole("button", { name: "검색" }).click();
  await expect(page).toHaveURL(new RegExp(`q=${MODULE}`));
  const card = page.getByRole("link", { name: new RegExp(MODULE) });
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("@alice-e2e");
  await expect(card).toContainText("Network for the explore test");
  await expect(card).toContainText(badge);
  await expect(card).toContainText("aws");
  await expect(card.getByTitle("별표")).toHaveText("0");
  await expect(card.getByTitle("가져간 수")).toHaveText("0");
  await shot(page, "33-explore");

  // 2. The module page shows README, inputs and the security summary.
  await card.click();
  await expect(page).toHaveURL(new RegExp(`/@alice-e2e/${MODULE}$`));
  await expect(page.locator(".markdown")).toContainText("A network module");
  await expect(page.getByText(badge).first()).toBeVisible();
  // Logged out, starring and forking lead to login.
  await expect(page.getByRole("link", { name: /^별표/ })).toHaveAttribute(
    "href",
    /\/api\/auth\/github\/login/,
  );

  // 3. Another user signs in.
  const bob = await signedIn(browser, "bob-e2e");
  await bob.page.goto(`/@alice-e2e/${MODULE}`);
  const star = bob.page.getByRole("button", { name: /^별표/ });
  await star.click();
  await expect(
    bob.page.getByRole("button", { name: /^별표 취소\s*1$/ }),
  ).toBeVisible();
  await bob.page.reload();
  await expect(
    bob.page.getByRole("button", { name: /^별표 취소\s*1$/ }),
  ).toBeVisible();

  // 4. Fork: the dialog shows the origin's security summary before anything is created.
  await bob.page.getByRole("button", { name: /내 저장소로 가져오기/ }).click();
  const summary = bob.page.getByRole("region", { name: "원본 보안 요약" });
  await expect(summary).toContainText(badge);
  await expect(summary).toContainText(
    `탐지 ${detail.version.scan.summary.total}건`,
  );
  await expect(summary).toContainText("라이선스: Apache-2.0");
  await expect(bob.page.getByLabel("이름")).toHaveValue(MODULE);
  await expect(bob.page.getByLabel("비공개")).toBeChecked();
  await shot(bob.page, "34-fork-dialog");
  await bob.page.getByRole("button", { name: "가져오기", exact: true }).click();
  await expect(bob.page).toHaveURL(new RegExp(`/@bob-e2e/${MODULE}$`));
  await expect(
    bob.page.getByRole("link", { name: `@alice-e2e/${MODULE}` }),
  ).toBeVisible();
  await expect(bob.page.getByText("비공개").first()).toBeVisible();
  await expect(bob.page.locator(".markdown")).toContainText("A network module");
  await expect(bob.page.getByText(badge).first()).toBeVisible();
  await shot(bob.page, "35-forked-module");

  // The origin's counters moved, and the visitor sees them.
  await page.goto(`/explore?q=${MODULE}`);
  await expect(card.getByTitle("별표")).toHaveText("1");
  await expect(card.getByTitle("가져간 수")).toHaveText("1");
  // Bob's fork is private: not in explore, not on his public profile, not by address.
  await expect(
    page.getByRole("link", { name: new RegExp(MODULE) }),
  ).toHaveCount(1);
  await page.goto("/@bob-e2e?tab=forked");
  await expect(page.getByText("가져온 모듈이 없습니다.")).toBeVisible();
  await page.goto(`/@bob-e2e/${MODULE}`);
  await expect(page.getByText("모듈을 찾을 수 없습니다")).toBeVisible();

  // 7. Bob's own profile lists what he forked and starred; Alice's lists what she published.
  await bob.page.getByRole("button", { name: "계정 메뉴: bob-e2e" }).click();
  await bob.page.getByRole("menuitem", { name: "내 프로필" }).click();
  await expect(bob.page).toHaveURL(/\/@bob-e2e$/);
  await bob.page.getByRole("link", { name: /^가져온 모듈/ }).click();
  const forked = bob.page.getByRole("link", { name: new RegExp(MODULE) });
  await expect(forked).toContainText(`원본: @alice-e2e/${MODULE}`);
  await shot(bob.page, "36-profile");
  await bob.page.getByRole("link", { name: /^별표/ }).click();
  await expect(
    bob.page.getByRole("link", { name: new RegExp(MODULE) }),
  ).toContainText("@alice-e2e");
  await page.goto("/@ALICE-E2E");
  await expect(
    page.getByRole("link", { name: new RegExp(MODULE) }),
  ).toBeVisible();

  // The fork keeps working when the origin is hidden.
  const hidden = await alice.context.request.patch(
    `${api}/api/repositories/${MODULE}`,
    { data: { visibility: "PRIVATE" }, ...write(alice.context) },
  );
  expect(hidden.status()).toBe(204);
  await bob.page.goto(`/@bob-e2e/${MODULE}?tab=security`);
  await expect(bob.page.getByText("다른 모듈에서 가져옴")).toBeVisible();
  await bob.page.getByRole("link", { name: "상세 보기 →" }).click();
  await expect(
    bob.page.getByRole("heading", { name: /위험도 분포/ }),
  ).toBeVisible();
  await page.goto(`/explore?q=${MODULE}`);
  await expect(page.getByText("조건에 맞는 모듈이 없습니다.")).toBeVisible();
});

test("an unknown profile is a real 404", async ({ page }) => {
  const response = await page.goto("/@nobody-at-all");
  expect(response?.status()).toBe(404);
});
