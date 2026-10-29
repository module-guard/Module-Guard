import { test, expect, type Page } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { makeZip } from "./zip";

async function login(page: Page) {
  await page.goto("/new");
  await page.getByRole("link", { name: "GitHub로 로그인" }).first().click();
  await expect(
    page.getByRole("button", { name: "계정 메뉴: dev-user" }),
  ).toBeVisible();
}

async function publishPrepared(page: Page, name: string) {
  await expect(page.getByRole("region", { name: "확인 결과" })).toBeVisible({
    timeout: 180000,
  });
  await page.getByLabel("이름", { exact: true }).fill(name);
  const acknowledge = page.getByLabel("비밀값이 아님을 확인했습니다");
  if (await acknowledge.isVisible()) await acknowledge.check();
  await page.getByRole("button", { name: "올리기", exact: true }).click();
  await expect(page).toHaveURL(/\/scans\/[a-f0-9-]+/);
  await expect(page.getByText("공개로 등록됨")).toBeVisible({
    timeout: 240000,
  });
  await expect(page.getByRole("region", { name: "모듈 무결성" })).toContainText(
    "원본 무결성 · 정상",
  );
  await page.goto(`/@dev-user/${name}?tab=use`);
}

async function verifyAndDownload(page: Page, name: string) {
  await expect(page.getByRole("region", { name: "모듈 무결성" })).toContainText(
    "원본 무결성 · 정상",
  );
  await page.getByRole("button", { name: "무결성 다시 검사" }).click();
  await expect(
    page.getByText("무결성 검증 완료", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "파일별 해시 확인" }).click();
  await expect(page.getByText(/파일별 SHA-256 \(/)).toBeVisible();
  const [download, preparation] = await Promise.all([
    page.waitForEvent("download"),
    page.waitForResponse(
      (r) =>
        r.url().endsWith("/download/prepare") &&
        r.request().method() === "POST",
    ),
    page.getByRole("button", { name: "zip 내려받기" }).click(),
  ]);
  const expected = (await preparation.json()).zip_sha256;
  const bytes = await readFile((await download.path())!);
  expect(createHash("sha256").update(bytes).digest("hex")).toBe(expected);
  expect(download.suggestedFilename()).toBe(`${name}-v1.zip`);
  await expect(page.getByText(/최종 ZIP SHA-256:/)).toContainText(expected);
  await page.screenshot({
    path: test.info().outputPath(`${name}-verified.png`),
    fullPage: true,
  });
}

test("upload → analysis → manifest → verified ZIP; altered received bytes cannot save", async ({
  page,
}) => {
  test.setTimeout(360000);
  await login(page);
  await page.getByRole("tab", { name: "zip 파일" }).click();
  await page
    .locator("#source-zip")
    .setInputFiles({
      name: "integrity.zip",
      mimeType: "application/zip",
      buffer: makeZip({
        "main.tf": 'resource "null_resource" "example" {}\n',
        "README.md": "# Integrity",
      }),
    });
  await page.getByRole("button", { name: "확인", exact: true }).click();
  await publishPrepared(page, "integrity-upload");
  await verifyAndDownload(page, "integrity-upload");
  let downloads = 0;
  page.on("download", () => downloads++);
  await page.route(
    "**/api/repositories/dev-user/integrity-upload/download",
    async (route) => {
      const response = await route.fetch();
      const bytes = Buffer.from(await response.body());
      bytes[0] ^= 1;
      await route.fulfill({ response, body: bytes });
    },
  );
  await page.getByRole("button", { name: "zip 내려받기" }).click();
  await expect(
    page.getByText("파일 변조 의심 — 다운로드 차단", { exact: true }),
  ).toBeVisible();
  expect(downloads).toBe(0);
  await page.screenshot({
    path: test.info().outputPath("tampered-download-blocked.png"),
    fullPage: true,
  });
});

for (const source of ["github", "registry"] as const) {
  test(`live ${source} collection → analysis → verified download`, async ({
    page,
  }) => {
    test.skip(
      process.env.RUN_LIVE_SCAN !== "true",
      "Opt-in network + real Checkov",
    );
    test.setTimeout(600000);
    await login(page);
    if (source === "github") {
      await page
        .getByLabel("GitHub 저장소 주소")
        .fill("https://github.com/terraform-aws-modules/terraform-aws-kms");
    } else {
      await page
        .getByRole("tab", { name: "Terraform Registry", exact: true })
        .click();
      await page
        .getByLabel("Terraform Registry 주소")
        .fill("terraform-aws-modules/kms/aws");
      await page.getByLabel("버전 (선택)").fill("4.2.2");
    }
    await page.getByRole("button", { name: "확인", exact: true }).click();
    await publishPrepared(page, `integrity-${source}`);
    await verifyAndDownload(page, `integrity-${source}`);
  });
}
