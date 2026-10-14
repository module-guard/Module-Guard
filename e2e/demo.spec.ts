import { execFileSync, spawnSync } from "node:child_process";
import { appendFileSync, mkdirSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test, expect, type Page } from "@playwright/test";
import { makeZip } from "./zip";

// PRODUCT_PLAN.md 2절 시연 시나리오 1~7을 발표 순서 그대로 한 흐름으로 돈다.
//   npm run test:demo   (= --repeat-each=5 --retries=0)
// 미러 vpc를 실제 GitHub·Checkov로 준비하므로 외부 접속이 필요하다: RUN_DEMO=true일 때만.
const api = process.env.PLAYWRIGHT_API_URL || "http://localhost:8000";
const MIRROR = "terraform-aws-modules-mirror";
const VPC = "terraform-aws-vpc";
const TIMINGS = "test-results/demo-timings.jsonl";
const hasTerraform = spawnSync("terraform", ["version"]).status === 0;
// 회차가 달라도 provider는 한 번만 내려받는다.
const pluginCache = join(tmpdir(), "mg-demo-plugin-cache");
// 6단계에 올리는 작은 모듈(발표에서는 kms 크기의 모듈을 쓴다).
const UPLOAD = {
  "main.tf":
    'resource "aws_kms_key" "this" {\n  description         = var.description\n  enable_key_rotation = true\n}\n',
  "variables.tf":
    'variable "description" {\n  type    = string\n  default = "demo key"\n}\n',
  "versions.tf":
    'terraform {\n  required_providers {\n    aws = { source = "hashicorp/aws" }\n  }\n}\n',
  "README.md": "# Demo KMS\n\nA small module uploaded during the demo.\n",
  LICENSE: "Apache License\nVersion 2.0, January 2004\n",
};

// 버튼이 없으면 10분을 기다리지 않고 30초 안에 그 단계 이름으로 실패한다.
test.use({ actionTimeout: 30000 });

test.skip(
  process.env.RUN_DEMO !== "true",
  "Demo scenario needs GitHub + Checkov: RUN_DEMO=true",
);

test.beforeAll(() => {
  // 이미 저장돼 있으면 'unchanged'라 두 번째 회차부터는 바로 끝난다.
  const output = execFileSync(
    "docker",
    [
      ...["compose", "-p", "module-guard-e2e", "-f", "docker-compose.e2e.yml"],
      ...["exec", "-T", "api", "python", "scripts/seed_mirrors.py"],
      `--only=${VPC}`,
      "--apply",
    ],
    { encoding: "utf8", timeout: 600000 },
  );
  expect(output).toMatch(new RegExp(`${VPC}\\s+\\S+\\s+(added|unchanged)`));
  mkdirSync(pluginCache, { recursive: true });
});

async function forkCount(page: Page) {
  const label =
    (await page
      .getByRole("link", { name: /내 저장소로 가져오기/ })
      .or(page.getByRole("button", { name: /내 저장소로 가져오기/ }))
      .first()
      .textContent()) || "";
  return Number(label.match(/(\d+)\s*$/)?.[1] ?? NaN);
}

test("demo scenario 1-7 in one flow", async ({ page }, info) => {
  test.setTimeout(600000);
  // 회차마다 다른 사람: 가져오기 이름, 시간당 제한, 내 모듈 목록이 섞이지 않는다.
  const login = `demo-${info.repeatEachIndex}-${Date.now().toString(36)}`;
  const upload = `demo-kms-${info.repeatEachIndex}`;
  const timings: Record<string, number> = {};
  const step = async (name: string, body: () => Promise<void>) => {
    const started = Date.now();
    await test.step(name, body);
    timings[name] = Date.now() - started;
  };
  let before = NaN;

  await step("1 탐색에서 vpc 검색", async () => {
    await page.goto("/explore");
    await page.getByLabel("모듈 검색").fill("vpc");
    await page.getByRole("button", { name: "검색" }).click();
    const card = page.locator(`a[href="/@${MIRROR}/${VPC}"]`);
    await expect(card).toBeVisible();
    await expect(card).toContainText(VPC);
    await expect(card).toContainText("미러");
    await expect(card).toContainText(/주요 위험 없음|주의 \d+종|위험 \d+종/);
    await expect(card.getByTitle("별표")).toHaveText(/^\d+$/);
    await expect(card.getByTitle("가져간 수")).toHaveText(/^\d+$/);
    await card.click();
  });

  await step("2 모듈 페이지: README, 입력, 보안", async () => {
    await expect(page).toHaveURL(new RegExp(`/@${MIRROR}/${VPC}$`));
    await expect(page.locator(".markdown h1").first()).toBeVisible();
    const tabs = page.getByRole("navigation", { name: "모듈 탭" });
    await tabs.getByRole("link", { name: "입력·출력" }).click();
    await expect(
      page.getByRole("cell", { name: "cidr", exact: true }),
    ).toBeVisible();
    await tabs.getByRole("link", { name: "보안" }).click();
    await expect(page.getByText("위험도 분포").first()).toBeVisible();
    await expect(page.getByText("Apache-2.0").first()).toBeVisible();
    before = await forkCount(page);
    expect(before).toBeGreaterThanOrEqual(0);
  });

  await step("3 GitHub로 로그인", async () => {
    await page
      .context()
      .addCookies([{ name: "mg_dev_login", value: login, url: api }]);
    await page
      .getByRole("banner")
      .getByRole("link", { name: "GitHub로 로그인" })
      .click();
    // 로그인을 시작한 모듈 페이지로 돌아온다.
    await expect(page).toHaveURL(new RegExp(`/@${MIRROR}/${VPC}`));
    await expect(
      page.getByRole("button", { name: `계정 메뉴: ${login}` }),
    ).toBeVisible();
  });

  await step("4 내 저장소로 가져오기", async () => {
    await page.getByRole("button", { name: /내 저장소로 가져오기/ }).click();
    const summary = page.getByRole("region", { name: "원본 보안 요약" });
    await expect(summary).toContainText(/탐지 \d+건/);
    await expect(summary).toContainText("라이선스: Apache-2.0");
    await page.getByRole("button", { name: "가져오기", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/@${login}/${VPC}$`));
    await expect(
      page.getByRole("link", { name: `@${MIRROR}/${VPC}` }),
    ).toBeVisible();
    await page.goto(`/@${MIRROR}/${VPC}`);
    expect(await forkCount(page)).toBe(before + 1);
  });

  await step("5 사용하기: 변수 입력, 코드, zip", async () => {
    await page.goto(`/@${login}/${VPC}?tab=use`);
    const main = page.getByLabel("main.tf", { exact: true });
    await expect(main).toContainText(`source = "./modules/${VPC}"`);
    await page.locator("#use-name").fill("demo-vpc");
    await page.locator("#use-cidr").fill("10.0.0.0/16");
    await page.locator("#use-enable_nat_gateway").selectOption("true");
    await expect(main).toContainText('"demo-vpc"');
    await expect(main).toContainText('"10.0.0.0/16"');
    await expect(main).toContainText("enable_nat_gateway");
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "zip 내려받기" }).click(),
    ]);
    expect(download.suggestedFilename()).toBe(`${VPC}-v1.zip`);
    const directory = mkdtempSync(join(tmpdir(), "mg-demo-"));
    await download.saveAs(join(directory, "module.zip"));
    execFileSync("unzip", [
      "-q",
      join(directory, "module.zip"),
      "-d",
      join(directory, "w"),
    ]);
    const work = join(directory, "w");
    expect(readFileSync(join(work, "main.tf"), "utf8")).toBe(
      (await main.textContent()) || "",
    );
    if (!hasTerraform) {
      info.annotations.push({
        type: "skipped",
        description: "terraform 없음: validate 생략",
      });
      return;
    }
    const run = (args: string[]) =>
      execFileSync("terraform", args, {
        cwd: work,
        encoding: "utf8",
        env: {
          ...process.env,
          TF_PLUGIN_CACHE_DIR: pluginCache,
          TF_IN_AUTOMATION: "1",
        },
        timeout: 300000,
      });
    run(["init", "-backend=false", "-input=false", "-no-color"]);
    expect(run(["validate", "-no-color"])).toContain("Success");
  });

  await step("6 올리기(zip) → 검사 → 공개", async () => {
    await page.goto("/new");
    await page.getByRole("tab", { name: "zip 파일" }).click();
    await page.locator("#source-zip").setInputFiles({
      name: `${upload}.zip`,
      mimeType: "application/zip",
      buffer: makeZip(UPLOAD),
    });
    await page.getByRole("button", { name: "확인", exact: true }).click();
    await expect(page.getByRole("region", { name: "확인 결과" })).toBeVisible({
      timeout: 60000,
    });
    await expect(page.getByLabel("이름")).toHaveValue(upload);
    await page.getByRole("button", { name: "올리기", exact: true }).click();
    // 검사 화면으로 넘어가 진행 상황이 보이고, 끝나면 공개로 등록된다.
    await expect(page).toHaveURL(new RegExp(`/@${login}/${upload}/scans/`));
    await expect(page.getByText("공개로 등록됨")).toBeVisible({
      timeout: 180000,
    });
  });

  await step("7 내 프로필: 올린 모듈과 가져온 모듈", async () => {
    await page.goto(`/@${login}`);
    await expect(page.locator(`a[href="/@${login}/${upload}"]`)).toBeVisible();
    await page
      .getByRole("navigation", { name: "프로필 탭" })
      .getByRole("link", { name: /가져온 모듈/ })
      .click();
    const forked = page.locator(`a[href="/@${login}/${VPC}"]`);
    await expect(forked).toBeVisible();
    await expect(forked).toContainText(`원본: @${MIRROR}/${VPC}`);
  });

  mkdirSync("test-results", { recursive: true });
  appendFileSync(
    TIMINGS,
    JSON.stringify({
      run: info.repeatEachIndex,
      at: new Date().toISOString(),
      timings,
    }) + "\n",
  );
});
