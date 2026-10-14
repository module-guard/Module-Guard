import { execFileSync, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test, expect, type Page } from "@playwright/test";
import { makeZip } from "./zip";

// The "use" tab: form → generated code → zip → the zip really works with terraform.
const PREFLIGHT = { timeout: 60000 };
const hasTerraform = spawnSync("terraform", ["version"]).status === 0;
// Every character here would break out of a string if it were not escaped.
const HOSTILE =
  'web "${var.password}" %{ if true }x%{ endif } } resource "terraform_data" "pwned" {} \\';
// A one-line input cannot hold a line break, so that attempt goes through a JSON value.
const BREAKOUT =
  'x"\n}\nresource "terraform_data" "pwned" {}\nlocals {\n  y = "';
const TAGS = { team: "${core}", note: BREAKOUT };
const MODULE = {
  // terraform_data is built in, so init and apply need no network.
  "main.tf":
    'resource "terraform_data" "this" {\n  input = { name = var.name, replicas = var.replicas, tags = var.tags }\n}\n',
  "variables.tf": [
    'variable "name" {\n  description = "Display name"\n  type        = string\n}',
    'variable "password" {\n  type      = string\n  sensitive = true\n}',
    'variable "replicas" {\n  type    = number\n  default = 1\n}',
    'variable "enabled" {\n  type    = bool\n  default = true\n}',
    'variable "tags" {\n  type    = map(string)\n  default = {}\n}',
    "",
  ].join("\n"),
  "outputs.tf":
    'output "name" {\n  value = var.name\n}\noutput "tags" {\n  value = var.tags\n}\n',
  "README.md": "# Use me\n",
};

async function shot(page: Page, name: string) {
  const directory = process.env.SCREENSHOT_DIR;
  if (!directory) return;
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${directory}/${name}.png`, fullPage: true });
}

async function login(page: Page) {
  await page.goto("/new");
  await page.getByRole("link", { name: "GitHub로 로그인" }).first().click();
  await expect(
    page.getByRole("button", { name: "계정 메뉴: dev-user" }),
  ).toBeVisible();
}

/** Clicks download, unpacks the zip into a fresh directory and returns its path. */
async function downloadInto(page: Page, expectedName: RegExp) {
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "zip 내려받기" }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(expectedName);
  const directory = mkdtempSync(join(tmpdir(), "mg-use-"));
  const archive = join(directory, "download.zip");
  await download.saveAs(archive);
  const work = join(directory, "work");
  execFileSync("unzip", ["-q", archive, "-d", work]);
  return work;
}

function terraform(
  cwd: string,
  args: string[],
  env: Record<string, string> = {},
) {
  return execFileSync("terraform", args, {
    cwd,
    encoding: "utf8",
    env: { ...process.env, TF_IN_AUTOMATION: "1", ...env },
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 600000,
  });
}

test("use tab: fill the form, get code, download a zip that terraform accepts", async ({
  page,
  browser,
}) => {
  test.setTimeout(240000);
  await login(page);
  await page.getByRole("tab", { name: "zip 파일" }).click();
  await page.locator("#source-zip").setInputFiles({
    name: "use-module.zip",
    mimeType: "application/zip",
    buffer: makeZip(MODULE),
  });
  await page.getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.getByRole("region", { name: "확인 결과" })).toBeVisible(
    PREFLIGHT,
  );
  await page.getByRole("button", { name: "올리기", exact: true }).click();
  await expect(page.getByText("공개로 등록됨")).toBeVisible({
    timeout: 120000,
  });

  // No login needed for a public module.
  const visitor = await (await browser.newContext()).newPage();
  await visitor.goto("/@dev-user/use-module");
  await visitor
    .getByRole("navigation", { name: "모듈 탭" })
    .getByRole("link", { name: "사용하기" })
    .click();
  await expect(visitor).toHaveURL(/tab=use$/);
  const main = visitor.getByLabel("main.tf", { exact: true });
  await expect(main).toContainText('source   = "./modules/use-module"');
  // The required value is missing, and the sensitive one is never typed in.
  await expect(visitor.getByRole("status")).toContainText(
    "필수 값 1개가 비어 있습니다",
  );
  await expect(main).toContainText("password = var.password");
  await expect(visitor.getByText("export TF_VAR_password=…")).toBeVisible();
  await expect(visitor.locator("input#use-password")).toHaveCount(0);

  await visitor.locator("#use-name").fill("my-vpc");
  await expect(main).toContainText('name     = "my-vpc"');
  await expect(visitor.getByText("필수 값 1개가 비어")).toHaveCount(0);
  await visitor.getByText("고급 설정").click();
  await visitor.locator("#use-replicas").fill("abc");
  await expect(
    visitor.getByRole("alert").filter({ hasText: "숫자" }),
  ).toBeVisible();
  await visitor.locator("#use-replicas").fill("3");
  await visitor.locator("#use-enabled").selectOption("false");
  await visitor.locator("#use-tags").fill("{ team = var.x }");
  await expect(
    visitor.getByRole("alert").filter({ hasText: "JSON" }),
  ).toBeVisible();
  await visitor.locator("#use-tags").fill('{"team": "${core}"}');
  await expect(main).toContainText('tags     = {"team" = "$${core}"}');
  await expect(main).toContainText("replicas = 3");
  await expect(main).toContainText("enabled  = false");
  await expect(
    visitor.getByLabel("variables.tf", { exact: true }),
  ).toContainText("sensitive = true");
  await shot(visitor, "39-module-use");

  // Hostile values stay strings: nothing new appears outside the quotes.
  await visitor.locator("#use-name").fill(HOSTILE);
  await visitor.locator("#use-tags").fill(JSON.stringify(TAGS));
  await expect(main).toContainText("$${var.password}");
  await expect(main).toContainText("\\n}\\nresource");
  const code = (await main.textContent()) || "";
  expect(code).toContain(
    '%%{ if true }x%%{ endif } } resource \\"terraform_data\\"',
  );
  expect(code.match(/^\s*(resource|locals) /m)).toBeNull();
  expect(code.trimEnd().split("\n")).toHaveLength(9);

  const work = await downloadInto(visitor, /^use-module-v1\.zip$/);
  expect(readFileSync(join(work, "main.tf"), "utf8")).toBe(code);
  expect(existsSync(join(work, "variables.tf"))).toBe(true);
  for (const name of Object.keys(MODULE))
    expect(readFileSync(join(work, "modules/use-module", name), "utf8")).toBe(
      MODULE[name as keyof typeof MODULE],
    );

  test.skip(!hasTerraform, "terraform is not installed");
  terraform(work, ["init", "-backend=false", "-input=false", "-no-color"]);
  expect(terraform(work, ["validate", "-no-color"])).toContain("Success");
  // Apply it and read the value back: it must be exactly what was typed, and nothing else
  // may have been created.
  writeFileSync(
    join(work, "check.tf"),
    'output "name" {\n  value = module.use-module.name\n}\noutput "tags" {\n  value = module.use-module.tags\n}\n',
  );
  const env = { TF_VAR_password: "from-the-environment" };
  terraform(work, ["apply", "-auto-approve", "-input=false", "-no-color"], env);
  const outputs = JSON.parse(terraform(work, ["output", "-json"], env));
  expect(outputs.name.value).toBe(HOSTILE);
  expect(outputs.tags.value).toEqual(TAGS);
  const state = terraform(work, ["state", "list"], env).trim().split("\n");
  expect(state).toEqual(["module.use-module.terraform_data.this"]);
});

// Real modules from GitHub, then the registry for providers and child modules: opt-in.
for (const name of ["terraform-aws-vpc", "terraform-aws-eks"]) {
  test(`live: ${name} downloads into a tree that passes init and validate`, async ({
    page,
  }) => {
    test.skip(
      process.env.RUN_LIVE_SCAN !== "true" || !hasTerraform,
      "Opt-in network + terraform",
    );
    test.setTimeout(1500000);
    await login(page);
    await page
      .locator("#source-url")
      .fill(`https://github.com/terraform-aws-modules/${name}`);
    await page.getByRole("button", { name: "확인", exact: true }).click();
    await expect(page.getByRole("region", { name: "확인 결과" })).toBeVisible({
      timeout: 300000,
    });
    await page.locator("#module-name").fill(name);
    await page.getByRole("button", { name: "올리기", exact: true }).click();
    await expect(page.getByText("공개로 등록됨")).toBeVisible({
      timeout: 900000,
    });
    await page.goto(`/@dev-user/${name}?tab=use`);
    const main = page.getByLabel("main.tf", { exact: true });
    await expect(main).toContainText(`source = "./modules/${name}"`);
    await expect(page.getByText("필수 값")).toHaveCount(0);
    await expect(
      page.getByText(/git::https:\/\/github\.com\/terraform-aws-modules\//),
    ).toBeVisible();
    if (name === "terraform-aws-vpc") await shot(page, "40-module-use-vpc");
    const work = await downloadInto(page, new RegExp(`^${name}-v1\\.zip$`));
    terraform(work, ["init", "-backend=false", "-input=false", "-no-color"]);
    const validated = terraform(work, ["validate", "-no-color"]);
    console.log(`LIVE_USE_RESULT ${name}: ${validated.trim().split("\n")[0]}`);
    expect(validated).toContain("Success");
  });
}
