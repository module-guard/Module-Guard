import { expect, type Locator, type Page } from "@playwright/test";
import { scanId, type Surface } from "../fixtures/graph-quality";

declare global {
  interface Window {
    graphQualityProbe: { since: number; shifts: number; longTasks: number[] };
  }
}

export const labels = {
  lineage: "신뢰 계보",
  comparison: "버전 비교",
  risk: "정적 위험 경로",
  dynamic: "동적 분석 비실행 데모",
};

export async function installProbe(page: Page) {
  await page.addInitScript(() => {
    const probe: Window["graphQualityProbe"] = (window.graphQualityProbe = {
      since: 0,
      shifts: 0,
      longTasks: [],
    });
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries())
        if (entry.startTime >= probe.since)
          probe.longTasks.push(entry.duration);
    }).observe({ type: "longtask", buffered: true });
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        const shift = entry as PerformanceEntry & {
          value: number;
          hadRecentInput: boolean;
        };
        if (entry.startTime >= probe.since && !shift.hadRecentInput)
          probe.shifts += shift.value;
      }
    }).observe({ type: "layout-shift", buffered: true });
  });
}

export async function paint(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
}

export async function openSurface(
  page: Page,
  surface: Surface,
  size: number,
  keyboard = false,
) {
  await page.goto(
    surface === "lineage" || surface === "comparison"
      ? "/@quality/fixture"
      : `/@quality/fixture/scans/${scanId}`,
  );
  await expect(
    page.getByRole("heading", {
      level: 1,
      name:
        surface === "lineage" || surface === "comparison"
          ? "@quality / fixture"
          : "coverage-fixture",
      exact: true,
    }),
  ).toBeVisible();
  await page.evaluate(() => {
    window.graphQualityProbe = Object.assign(window.graphQualityProbe, {
      since: performance.now(),
      shifts: 0,
      longTasks: [],
    });
  });
  const start = await page.evaluate(() => performance.now());
  if (surface === "lineage" || surface === "comparison") {
    const link = page.getByRole("link", { name: "신뢰·비교", exact: true });
    if (keyboard) {
      await tabTo(page, link);
      await page.keyboard.press("Enter");
    } else await link.click();
    if (surface === "lineage") {
      const direction = page.getByRole("combobox", {
        name: "탐색 방향",
        exact: true,
      });
      // Fixture setup: native popup arrow keys are not simulated by macOS headless Chrome.
      // The node-list selection → evidence workflow below uses only Tab/Enter.
      await direction.selectOption("descendants");
      await expect(direction).toHaveValue("descendants");
      await expect(
        page.getByRole("region", { name: labels.lineage, exact: true }),
      ).toContainText(`접근 가능한 노드 ${size}개`);
    }
  } else {
    const trigger = page.getByText(
      surface === "risk"
        ? "정적 위험 경로·코드 근거 열기"
        : "동적 분석 비실행 데모 열기",
      { exact: true },
    );
    if (keyboard) {
      await tabTo(page, trigger);
      await page.keyboard.press("Enter");
    } else await trigger.click();
  }
  const region = page.getByRole("region", {
    name: labels[surface],
    exact: true,
  });
  await expect(
    region.locator(".react-flow__node-entity").first(),
  ).toBeVisible();
  await expect(region.getByText(/배치 계산 중/)).toHaveCount(0);
  await paint(page);
  return {
    region,
    firstDisplayMs: await page.evaluate(
      (start) => performance.now() - start,
      start,
    ),
  };
}

export function selection(region: Locator, surface: Surface, last = false) {
  switch (surface) {
    case "lineage":
      return region
        .getByRole("list", { name: "계보 목록 대체 보기" })
        .getByRole("button")
        .nth(last ? 1 : 0);
    case "comparison":
      return region
        .getByRole("list", { name: "버전 변경 목록" })
        .locator("summary")
        .nth(last ? 1 : 0);
    case "risk":
      return region
        .getByRole("list", { name: "위험 경로 목록" })
        .getByRole("button")
        .nth(last ? 1 : 0);
    case "dynamic":
      return region.locator("ol button").nth(last ? 1 : 0);
  }
}

export async function detailVisible(
  region: Locator,
  surface: Surface,
  second = false,
) {
  if (surface === "comparison") {
    const detail = region
      .getByRole("list", { name: "버전 변경 목록" })
      .locator("details")
      .nth(second ? 1 : 0);
    await expect(detail).toHaveAttribute("open", "");
    await expect(detail).toContainText("modules/network/main.tf");
  } else {
    const detail = region.getByRole("complementary", {
      name:
        surface === "lineage"
          ? "계보 상세"
          : surface === "risk"
            ? "코드 근거"
            : "비밀 제거된 이벤트 근거",
      exact: true,
    });
    await expect(detail).toBeVisible();
    await expect(detail).toContainText(
      surface === "lineage"
        ? "SHA-256"
        : surface === "risk"
          ? `CKV_TEST_${second ? 1 : 0}`
          : `evidence_${second ? 1 : 0}`,
    );
  }
}

export async function tabTo(page: Page, target: Locator) {
  // Continue the actual tab sequence from the screen's entry control; never force focus.
  if (await target.evaluate((node) => node === document.activeElement))
    return 0;
  for (let i = 0; i < 400; i++) {
    await page.keyboard.press("Tab");
    if (await target.evaluate((node) => node === document.activeElement))
      return i + 1;
  }
  throw new Error(
    "The evidence control is not reachable in the keyboard tab order",
  );
}
