import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test, expect, type Page } from "@playwright/test";

// Seeds a real mirror through the e2e API container (GitHub + Checkov): opt-in.
const api = process.env.PLAYWRIGHT_API_URL || "http://localhost:8000";
const MIRROR = "terraform-aws-modules-mirror";
const MODULE = "terraform-aws-kms";

async function shot(page: Page, name: string) {
  const directory = process.env.SCREENSHOT_DIR;
  if (!directory) return;
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${directory}/${name}.png`, fullPage: true });
}

test("mirror: published through the normal path, labelled, and impossible to sign in to", async ({
  page,
  browser,
}) => {
  test.skip(process.env.RUN_LIVE_SCAN !== "true", "Opt-in network + Checkov");
  test.setTimeout(900000);
  const seed = (...extra: string[]) =>
    execFileSync(
      "docker",
      [
        ...[
          "compose",
          "-p",
          "module-guard-e2e",
          "-f",
          "docker-compose.e2e.yml",
        ],
        ...["exec", "-T", "api", "python", "scripts/seed_mirrors.py"],
        `--only=${MODULE}`,
        ...extra,
      ],
      { encoding: "utf8", timeout: 800000 },
    );
  const applied = seed("--apply");
  expect(applied).toMatch(
    new RegExp(`${MODULE}\\s+v4\\.2\\.2\\s+(added|unchanged)`),
  );
  // Running it again adds nothing.
  expect(seed("--apply")).toContain("TOTAL: unchanged 1");

  await page.goto("/explore");
  await page.getByLabel("모듈 검색").fill("kms");
  await page.getByRole("button", { name: "검색" }).click();
  const card = page.locator(`a[href="/@${MIRROR}/${MODULE}"]`);
  await expect(card).toContainText("미러");
  await expect(card.getByTitle("별표")).toHaveText("0");
  await expect(card.getByTitle("가져간 수")).toHaveText("0");
  await page.getByLabel("모듈 검색").fill("");
  await page.getByRole("button", { name: "검색" }).click();
  await shot(page, "41-explore-mirrors");

  await card.click();
  await expect(page).toHaveURL(new RegExp(`/@${MIRROR}/${MODULE}$`));
  await expect(page.getByText("미러", { exact: true }).first()).toBeVisible();
  await expect(
    page.getByRole("link", {
      name: "github.com/terraform-aws-modules/terraform-aws-kms",
    }),
  ).toHaveAttribute(
    "href",
    "https://github.com/terraform-aws-modules/terraform-aws-kms",
  );
  await expect(page.getByText("@ v4.2.2")).toBeVisible();
  await expect(page.getByText("Apache-2.0").first()).toBeVisible();
  await shot(page, "42-mirror-module");

  // The downloaded mirror works as is.
  if (spawnSync("terraform", ["version"]).status === 0) {
    await page.goto(`/@${MIRROR}/${MODULE}?tab=use`);
    await expect(page.getByLabel("main.tf", { exact: true })).toContainText(
      `source = "./modules/${MODULE}"`,
    );
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "zip 내려받기" }).click(),
    ]);
    const directory = mkdtempSync(join(tmpdir(), "mg-mirror-"));
    await download.saveAs(join(directory, "m.zip"));
    execFileSync("unzip", [
      "-q",
      join(directory, "m.zip"),
      "-d",
      join(directory, "w"),
    ]);
    const run = (args: string[]) =>
      execFileSync("terraform", args, {
        cwd: join(directory, "w"),
        encoding: "utf8",
      });
    run(["init", "-backend=false", "-input=false", "-no-color"]);
    expect(run(["validate", "-no-color"])).toContain("Success");
  }

  await page.goto(`/@${MIRROR}`);
  await expect(page.getByText("미러 계정")).toBeVisible();
  await shot(page, "43-mirror-profile");

  // Signing in with the mirror's name or a reserved one gets a gh-<id> login. (The fake
  // provider only issues lowercase logins; mixed case is covered by the backend tests.)
  for (const login of [MIRROR, "terraform-aws-modules", "admin"]) {
    const context = await browser.newContext();
    await context.addCookies([
      { name: "mg_dev_login", value: login, url: api },
    ]);
    const impostor = await context.newPage();
    await impostor.goto("/new");
    await impostor
      .getByRole("link", { name: "GitHub로 로그인" })
      .first()
      .click();
    await expect(
      impostor.getByRole("button", { name: /계정 메뉴: gh-\d+/ }),
    ).toBeVisible();
    await impostor.goto(`/@${MIRROR}/${MODULE}?tab=settings`);
    await expect(impostor.getByText("모듈 삭제")).toHaveCount(0);
    await expect(
      impostor.getByRole("link", { name: "새 버전 올리기" }),
    ).toHaveCount(0);
    await context.close();
  }
  await page.goto(`/@${MIRROR}`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "terraform-aws-modules 미러",
  );
});
