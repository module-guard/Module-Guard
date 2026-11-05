import { test, expect } from "@playwright/test";
import { makeZip } from "./zip";
const api = process.env.PLAYWRIGHT_API_URL || "http://localhost:8001";

test("real API: publish, trust, comparison, reputation, risk and non-executing demo", async ({
  page,
}) => {
  test.setTimeout(180000);
  await page.goto("/new");
  await page.getByRole("link", { name: "GitHub로 로그인" }).first().click();
  await expect(
    page.getByRole("button", { name: "계정 메뉴: dev-user" }),
  ).toBeVisible();
  const name = `trust-${Date.now()}`;
  const headers = { Origin: "http://localhost:3100" };
  const prepared = await page.request.post(`${api}/api/uploads/zip`, {
    headers: { ...headers, "Content-Type": "application/zip" },
    data: makeZip({
      "main.tf":
        'resource "aws_security_group" "demo" {\n name = "demo"\n ingress {\n from_port = 22\n to_port = 22\n protocol = "tcp"\n cidr_blocks = ["0.0.0.0/0"]\n }\n}\n',
      "README.md": "# integration",
    }),
  });
  expect(prepared.ok()).toBeTruthy();
  const created = await page.request.post(`${api}/api/repositories`, {
    headers,
    data: {
      name,
      upload_id: (await prepared.json()).upload_id,
      visibility: "PUBLIC",
    },
  });
  expect(created.status()).toBe(201);
  const scanId = (await created.json()).scan_id;
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`${api}/api/scans/${scanId}`)).json())
          .status,
      { timeout: 90000 },
    )
    .toBe("COMPLETED");
  await page.goto(`/@dev-user/${name}`);
  await page.getByRole("link", { name: "신뢰·비교", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "출처·계보 탐색" }),
  ).toBeVisible();
  await expect(
    page.getByRole("list", { name: "계보 목록 대체 보기" }),
  ).toContainText(name);
  await page
    .getByRole("list", { name: "계보 목록 대체 보기" })
    .getByRole("button")
    .first()
    .focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("complementary", { name: "계보 상세" }),
  ).toContainText("SHA-256");
  await expect(page.getByRole("region", { name: "버전 비교" })).toContainText(
    "동일",
  );
  await expect(
    page.getByRole("region", { name: "검증 타임라인" }),
  ).toContainText("unsigned");
  await expect(page.getByRole("region", { name: "검사 재사용" })).toContainText(
    "측정 기록 없음",
  );
  await page.getByRole("link", { name: "평판 근거", exact: true }).click();
  await page.getByRole("button", { name: "공개 평판 근거 수집" }).click();
  await expect(page.getByRole("region", { name: "평판 근거" })).toContainText(
    "evidence-v1",
  );
  await expect(page.getByRole("region", { name: "평판 근거" })).toContainText(
    "해당 없음",
  );
  await page.goto(`/@dev-user/${name}/scans/${scanId}`);
  await page
    .getByText("정적 위험 경로·코드 근거 열기", { exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "정적 위험 경로" }),
  ).toContainText("실제 실행·통신을 관측한 결과가 아닙니다");
  await expect(
    page.getByRole("complementary", { name: "코드 근거" }),
  ).toBeVisible();
  await page.getByText("동적 분석 비실행 데모 열기", { exact: true }).focus();
  await page.keyboard.press("Enter");
  const demo = page.getByRole("region", { name: "동적 분석 비실행 데모" });
  await expect(demo).toContainText("실제 worker 미연결");
  for (const status of [
    "failed",
    "aborted",
    "timeout",
    "not_run",
    "unobservable",
    "success",
  ]) {
    await demo.getByLabel("데모 시나리오").selectOption(status);
    await expect(demo.getByText(/실행 ID:/)).toBeVisible();
    await expect(demo).toContainText("실제 실행: 없음");
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(demo).toBeVisible();
  expect(await demo.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(
    true,
  );
  await page.screenshot({
    path: "test-results/final-integration-mobile.png",
    fullPage: true,
  });
});
