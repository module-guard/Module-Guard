import { expect, test, type Page } from "@playwright/test";
import type { Scan } from "../src/components/live-shared";
import {
  completeAnalysis,
  partialAnalysis,
  scanFixture,
} from "./fixtures/analysis-coverage";

async function openScan(page: Page, scan: Scan) {
  const scanRequests: string[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    expect(request.method()).toBe("GET");
    const path = new URL(request.url()).pathname;
    let json: unknown;
    if (path === `/api/scans/${scan.id}`) {
      json = scan;
      scanRequests.push(path);
    } else if (path === "/api/me")
      json = { user: null, login_available: false };
    else if (path === "/api/health")
      json = { status: "ok", database: "connected", checkov: "available" };
    else if (path.endsWith("/dependencies"))
      json = { modules: [], providers: [], analysis: scan.analysis };
    else if (path.endsWith("/graph"))
      json = {
        schema_version: 1,
        nodes: [],
        edges: [],
        analysis: scan.analysis,
      };
    else if (path.endsWith("/flows"))
      json = {
        schema_version: 1,
        nodes: [],
        edges: [],
        summary: { total_flows: 0 },
      };
    else if (/\/(findings|correlations|entities)$/.test(path)) json = [];
    else throw new Error(`Unexpected API request: ${path}`);
    await route.fulfill({
      json,
      headers: {
        "access-control-allow-origin": request.headers().origin ?? "*",
        "access-control-allow-credentials": "true",
      },
    });
  });
  await page.goto(`/@fixture/coverage/scans/${scan.id}`);
  await expect(
    page.getByRole("heading", { level: 1, name: scan.module.name }),
  ).toBeVisible();
  return scanRequests;
}

function coverageSummary(page: Page) {
  return page.locator("summary").filter({ hasText: "분석 범위·누락 원인" });
}

for (const viewport of [
  { width: 1280, height: 900 },
  { width: 375, height: 812 },
]) {
  test(`partial diagnostics: keyboard, search, pagination and layout at ${viewport.width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize(viewport);
    const diagnostics = [
      ...partialAnalysis.diagnostics!,
      ...Array.from({ length: 42 }, (_, i) => ({
        code: "FILE_PARSE_FAILED",
        file_path: `file-${i.toString().padStart(2, "0")}.tf`,
        module_path: [],
      })),
      {
        code: "DYNAMIC_ITERATOR_FLOW_UNRESOLVED",
        file_path: `modules/${"long-folder-".repeat(20)}/main.tf`,
        module_path: ["nested", "dynamic"],
        message: "SECRET_LONG_MESSAGE",
      },
    ];
    const requests = await openScan(
      page,
      scanFixture({ analysis: { ...partialAnalysis, diagnostics } }),
    );
    await expect(
      page.getByText(/일부 분석이 완료되지 않았습니다/),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: /탐지 0/ })).toBeVisible();
    const summary = coverageSummary(page);
    const search = page.getByRole("searchbox", { name: "진단 검색" });
    await expect(search).not.toBeVisible();
    const initialPageWidth = await page.evaluate(
      () => document.documentElement.scrollWidth,
    );
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(summary.locator("..")).toHaveAttribute("open", "");
    await page.keyboard.press("Tab");
    await expect(search).toBeFocused();
    await expect(
      page
        .getByRole("list", { name: "분석 진단", exact: true })
        .getByRole("listitem"),
    ).toHaveCount(20);
    await expect(page.getByText("broken.tf", { exact: true })).toBeVisible();
    await search.fill("network");
    const list = page.getByRole("list", { name: "분석 진단", exact: true });
    await expect(list.getByRole("listitem")).toHaveCount(2);
    await expect(list).toContainText("modules/network/security.tf.json");
    await expect(list).toContainText("실제 인스턴스는 확장하지 않았습니다");
    await expect(list).toContainText("구성 요소 수 상한");
    await search.fill("SECRET_RAW_STDERR");
    await expect(
      page.getByText("일치하는 진단이 없습니다.", { exact: true }),
    ).toBeVisible();
    await search.fill("");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("button", { name: "다음 진단" })).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByText("2 / 3 페이지")).toBeVisible();
    await expect(list).not.toContainText("broken.tf");
    await search.fill("FILE_PARSE_FAILED");
    await expect(page.getByText("1 / 3 페이지")).toBeVisible();
    await search.fill("동적 반복자");
    await expect(list.getByRole("listitem")).toHaveCount(1);
    // The existing signed-out header can overflow on mobile. Coverage must fit
    // the viewport itself and must not increase the page's existing width.
    const coverage = page.getByRole("region", { name: "분석 범위 안내" });
    const bounds = await coverage.boundingBox();
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(viewport.width);
    expect(
      await coverage.evaluate(
        (element) => element.scrollWidth <= element.clientWidth,
      ),
    ).toBe(true);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(initialPageWidth);
    expect(
      await search.evaluate((input) => getComputedStyle(input).fontSize),
    ).toBe("16px");
    await expect(page.locator("body")).not.toContainText("SECRET_");
    await page.screenshot({
      path: testInfo.outputPath("coverage.png"),
      fullPage: true,
    });
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(search).not.toBeVisible();
    expect(requests).toHaveLength(1); // Expanding/filtering/paging needs no extra API requests.
  });
}

test("every analysis tab can open summary diagnostics and move keyboard focus there", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openScan(page, scanFixture({ analysis: partialAnalysis }));
  const tabs = page.getByRole("navigation", { name: "검사 결과 탭" });
  for (const name of [
    "보안 설정",
    "공급망",
    "의존성",
    "데이터 흐름",
    "상관 위험",
  ]) {
    await tabs.getByRole("button", { name: new RegExp(`^${name}`) }).click();
    await expect(
      page.getByRole("complementary", { name: "분석 범위 안내" }),
    ).toContainText("일부 분석");
    await page
      .getByRole("button", { name: "분석 범위·누락 원인 보기" })
      .click();
    await expect(
      tabs.getByRole("button", { name: "요약", exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(coverageSummary(page)).toBeFocused();
    await expect(page.getByText("broken.tf", { exact: true })).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(
      page.getByRole("searchbox", { name: "진단 검색" }),
    ).not.toBeVisible();
  }
  expect(errors).toEqual([]);
});

test("a completed scan with a failed stage is explained even without diagnostics", async ({
  page,
}) => {
  await openScan(
    page,
    scanFixture({
      analysis: {
        ...completeAnalysis,
        stages: { data_flow: { status: "FAILED" } },
      },
    }),
  );
  await expect(page.getByText(/일부 분석이 완료되지 않았습니다/)).toBeVisible();
  await coverageSummary(page).click();
  await expect(
    page.getByRole("list", { name: "완료되지 않은 분석 단계" }),
  ).toContainText("데이터 흐름");
  await expect(page.getByText(/기록된 진단이 없습니다/)).toBeVisible();
});

test("failed scans retain visible partial details, unlike completed successful scans", async ({
  page,
}) => {
  await openScan(
    page,
    scanFixture({ status: "FAILED", analysis: partialAnalysis }),
  );
  await expect(
    page.getByText(/검사가 실패했습니다. 저장된 부분 결과/),
  ).toBeVisible();
  await coverageSummary(page).click();
  await expect(page.getByText("broken.tf", { exact: true })).toBeVisible();
  await expect(page.getByText(/분석 단계가 모두 완료되었습니다/)).toHaveCount(
    0,
  );
});

test("successful scans show recorded completion and empty diagnostics without an incomplete warning", async ({
  page,
}) => {
  await openScan(page, scanFixture());
  await expect(page.getByText(/분석 단계가 모두 완료되었습니다/)).toBeVisible();
  await expect(page.getByText(/일부 분석이 완료되지 않았습니다/)).toHaveCount(
    0,
  );
  await coverageSummary(page).click();
  await expect(page.getByText(/기록된 진단이 없습니다/)).toBeVisible();
});

test("legacy scans with no analysis show missing information, not zero failures or full coverage", async ({
  page,
}) => {
  await openScan(page, scanFixture({ analysis: null }));
  await expect(page.getByText(/분석 범위 상세 정보가 없습니다/)).toBeVisible();
  await coverageSummary(page).click();
  await expect(
    page.getByText(/파일 집계와 상세 진단이 저장되지 않은 검사/),
  ).toBeVisible();
  await expect(page.getByText("분석 실패 파일", { exact: true })).toHaveCount(
    0,
  );
});
