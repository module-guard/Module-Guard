import { test, expect } from "@playwright/test";
const backend = process.env.PLAYWRIGHT_API_URL || "http://localhost:8000";
test("scan form → actual fixture pipeline → results, graph, trace and attack path", async ({
  page,
  request,
}) => {
  // Route only the collector entry to the development fixture. Responses come from the real backend.
  await page.route("**/api/scans", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const response = await request.post(`${backend}/api/dev/demo/correlation`);
    expect(response.status()).toBe(202);
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: await response.text(),
    });
  });
  await page.goto("/scan");
  await page
    .getByLabel("GitHub 저장소 주소")
    .fill("https://github.com/example/fixture");
  await page.getByRole("button", { name: "검사 시작", exact: true }).click();
  await expect(page).toHaveURL(/\/scans\/[a-f0-9-]+/);
  await expect(
    page.getByText("개발용 Fixture 시나리오 · Checkov 입력", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("완료", { exact: true }).first()).toBeVisible({
    timeout: 30000,
  });
  await page.getByRole("button", { name: /^보안 설정/ }).click();
  await expect(page.getByText("CKV_AWS_62", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^공급망/ }).click();
  await expect(page.getByText("TFSC004", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^의존성/ }).click();
  await expect(page.getByRole("img", { name: "의존성 그래프" })).toBeVisible();
  await page.getByRole("button", { name: /^데이터 흐름/ }).click();
  await page
    .getByLabel("Source address", { exact: true })
    .fill("module.policy_source.policy");
  await page
    .getByLabel("Target address", { exact: true })
    .fill("aws_iam_role_policy.admin.policy");
  await page.getByRole("button", { name: "흐름 추적", exact: true }).click();
  await expect(
    page.locator("ol").getByText("↓ local.policy", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /^상관 위험/ }).click();
  await expect(page.getByText("TFCR001", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "근거 및 영향 경로 보기" }).click();
  await expect(
    page.getByRole("region", { name: "상관 분석 근거" }),
  ).toContainText("MEDIUM");
  await expect(
    page.getByRole("region", { name: "상관 분석 근거" }),
  ).toContainText("local.policy");
  await page.screenshot({
    path: "test-results/correlation-ui.png",
    fullPage: true,
  });
  // A development scan has no module page; in development mode its old address stays.
  await page.reload();
  await expect(page).toHaveURL(/\/scans\/[a-f0-9-]+$/);
  await expect(
    page.getByText("개발용 Fixture 시나리오 · Checkov 입력", { exact: false }),
  ).toBeVisible();
});
test("development page and actual health", async ({ page }) => {
  await page.goto("/dev/analysis-test");
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "terraform-aws-vpc 예제 주소 입력" })
    .click();
  await expect(page.getByLabel("저장소 주소", { exact: true })).toHaveValue(
    "https://github.com/terraform-aws-modules/terraform-aws-vpc",
  );
  await expect(page.getByLabel("검사 ID", { exact: true })).toHaveValue("");
  await page
    .getByRole("button", { name: "상관 위험 데모 실행", exact: true })
    .click();
  await expect(page.getByText("완료", { exact: true }).first()).toBeVisible({
    timeout: 30000,
  });
  await page.getByRole("button", { name: "API 결과 확인" }).click();
  await expect(page.getByText("성공", { exact: true })).toHaveCount(6);
});
test("backend failure is visible to users", async ({ page }) => {
  await page.route("**/api/repositories?*", (route) => route.abort());
  await page.goto("/explore");
  await expect(
    page.getByRole("alert").filter({ hasText: "백엔드에 연결" }),
  ).toContainText("백엔드에 연결");
  await expect(page.getByRole("button", { name: "다시 시도" })).toBeVisible();
});

test("live public repository through the browser", async ({
  page,
  request,
}, testInfo) => {
  test.skip(
    process.env.RUN_LIVE_SCAN !== "true",
    "Opt-in network + Checkov integration",
  );
  test.setTimeout(600000);
  await page.goto("/scan");
  await page
    .getByLabel("GitHub 저장소 주소")
    .fill("https://github.com/terraform-aws-modules/terraform-aws-vpc");
  await page.getByRole("button", { name: "검사 시작", exact: true }).click();
  await expect(page).toHaveURL(/\/scans\/[a-f0-9-]+/);
  const scanId = page.url().split("/").pop()!;
  await expect
    .poll(
      async () =>
        (await (await request.get(`${backend}/api/scans/${scanId}`)).json())
          .status,
      { timeout: 580000, intervals: [2000] },
    )
    .toMatch(/COMPLETED|FAILED/);
  const scan = await (
    await request.get(`${backend}/api/scans/${scanId}`)
  ).json();
  expect(scan.status).toBe("COMPLETED");
  console.log(
    "LIVE_REPOSITORY_RESULT",
    JSON.stringify({
      id: scan.id,
      commit: scan.commit_hash,
      duration_ms: scan.duration_ms,
      entities: scan.analysis.entities,
      dependency_edges: scan.analysis.dependency_edges,
      data_flow: scan.analysis.data_flow,
      scanner_breakdown: scan.analysis.scanner_breakdown,
    }),
  );
  await testInfo.attach("live-scan.json", {
    body: JSON.stringify(scan, null, 2),
    contentType: "application/json",
  });
  await page.getByRole("button", { name: /^상관 위험/ }).click();
  await expect(
    page.getByText("연결된 고위험 경로가 발견되지 않았습니다.", {
      exact: true,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/live-repository-ui.png",
    fullPage: true,
  });
});
