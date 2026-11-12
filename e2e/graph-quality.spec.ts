import { writeFile } from "node:fs/promises";
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { mockGraphQuality, sizes, surfaces } from "./fixtures/graph-quality";
import {
  detailVisible,
  installProbe,
  labels,
  openSurface,
  paint,
  selection,
  tabTo,
} from "./helpers/graph-quality";

const baseline = process.env.GRAPH_QUALITY_BASELINE === "1";
test.use({ actionTimeout: 10000 });
if (baseline && process.env.CI)
  throw new Error("CI must enforce graph budgets");
// Local baseline and CI headroom are documented in e2e/README.md.
const budgets = {
  firstDisplayMs: 6000,
  selectionMs: 2000,
  cumulativeLayoutShift: 0.1,
  maxLongTaskMs: 1000,
  heapUsedMiB: 128,
  heapGrowthMiB: 16,
};

test("performance probe observes a main-thread long task", async ({ page }) => {
  await mockGraphQuality(page, 20);
  await installProbe(page);
  await page.goto("/@quality/fixture");
  expect(
    await page.evaluate(() =>
      ["longtask", "layout-shift"].every((type) =>
        PerformanceObserver.supportedEntryTypes.includes(type),
      ),
    ),
  ).toBe(true);
  await page.evaluate(() => {
    setTimeout(() => {
      const end = performance.now() + 100;
      while (performance.now() < end) {
        /* deliberate probe calibration */
      }
    }, 0);
  });
  await expect
    .poll(() =>
      page.evaluate(() => Math.max(0, ...window.graphQualityProbe.longTasks)),
    )
    .toBeGreaterThanOrEqual(90);
});

test("dynamic evidence selection retains the existing graph layout", async ({
  page,
}) => {
  await mockGraphQuality(page, 1000);
  await installProbe(page);
  const { region } = await openSurface(page, "dynamic", 1000);
  const observed = region.evaluate(
    (node) =>
      new Promise<number>((resolve) => {
        let restarts = 0;
        const observer = new MutationObserver((records) => {
          for (const record of records)
            for (const added of record.addedNodes) {
              if (
                added instanceof HTMLElement &&
                added.getAttribute("role") === "status" &&
                added.textContent?.includes("배치 계산 중")
              )
                restarts++;
            }
        });
        observer.observe(node, { childList: true, subtree: true });
        setTimeout(() => {
          observer.disconnect();
          resolve(restarts);
        }, 500);
      }),
  );
  await selection(region, "dynamic").click();
  await detailVisible(region, "dynamic");
  expect(await observed).toBe(0);
});

for (const surface of surfaces)
  for (const size of sizes) {
    test(`graph performance: ${surface}, ${size} items`, async ({
      page,
    }, info) => {
      await page.setViewportSize({ width: 1280, height: 900 });
      const requests = await mockGraphQuality(page, size);
      await installProbe(page);
      const { region, firstDisplayMs } = await openSurface(page, surface, size);
      const cdp = await page.context().newCDPSession(page);
      await cdp.send("HeapProfiler.collectGarbage");
      const before = await cdp.send("Runtime.getHeapUsage");
      const selections: number[] = [];
      for (let i = 0; i < 5; i++) {
        if (
          surface === "comparison" &&
          (await selection(region, surface, i % 2 === 1).evaluate(
            (node) => (node.parentElement as HTMLDetailsElement).open,
          ))
        )
          await selection(region, surface, i % 2 === 1).click();
        const start = await page.evaluate(() => performance.now());
        await selection(region, surface, i % 2 === 1).click();
        await detailVisible(region, surface, i % 2 === 1);
        await paint(page);
        selections.push(
          await page.evaluate((start) => performance.now() - start, start),
        );
      }
      await expect(region.getByText(/배치 계산 중/)).toHaveCount(0);
      await cdp.send("HeapProfiler.collectGarbage");
      const after = await cdp.send("Runtime.getHeapUsage");
      const probe = await page.evaluate(() => window.graphQualityProbe);
      const metrics = {
        surface,
        size,
        viewport: 1280,
        firstDisplayMs,
        selectionMs: Math.max(...selections),
        cumulativeLayoutShift: probe.shifts,
        maxLongTaskMs: Math.max(0, ...probe.longTasks),
        longTaskTotalMs: probe.longTasks.reduce((a, b) => a + b, 0),
        heapUsedMiB: after.usedSize / 1048576,
        heapGrowthMiB: (after.usedSize - before.usedSize) / 1048576,
        renderedNodes: await region.locator(".react-flow__node-entity").count(),
        renderedStages:
          surface === "dynamic"
            ? await region.locator("ol > li").count()
            : null,
        requests,
      };
      console.log(`GRAPH_QUALITY ${JSON.stringify(metrics)}`);
      const metricsPath = info.outputPath("graph-performance.json");
      await writeFile(metricsPath, JSON.stringify(metrics, null, 2));
      await info.attach("graph-performance.json", {
        path: metricsPath,
        contentType: "application/json",
      });
      const expectedNodes =
        surface === "lineage"
          ? Math.min(size, 20) + 1
          : surface === "comparison"
            ? Math.min(size, 50)
            : surface === "risk"
              ? Math.min(size, 201)
              : size;
      const finder = region.getByRole("combobox", { name: /노드 찾기$/ });
      await expect(finder.locator("option")).toHaveCount(expectedNodes + 1);
      if (!baseline)
        for (const key of Object.keys(budgets) as (keyof typeof budgets)[])
          expect(
            metrics[key],
            `${surface}/${size}: ${key}`,
          ).toBeLessThanOrEqual(budgets[key]);
      expect(
        requests
          .filter((r) => r.path.endsWith("/lineage/graph"))
          .every((r) => r.returned <= 20),
      ).toBe(true);
      expect(
        requests
          .filter((r) => r.path.endsWith("/trust/compare"))
          .every((r) => r.returned <= 50),
      ).toBe(true);
      expect(
        requests
          .filter((r) => r.path.endsWith("/risk-paths"))
          .every((r) => r.returned <= 20),
      ).toBe(true);
      if (surface === "dynamic")
        expect(metrics.renderedStages).toBeLessThanOrEqual(100);
      if (size > 50 && surface !== "dynamic") {
        await region
          .getByRole("button", {
            name:
              surface === "lineage"
                ? "다음 계보"
                : surface === "comparison"
                  ? "다음 변경"
                  : "다음 위험",
            exact: true,
          })
          .click();
        await expect.poll(() => requests.some((r) => r.offset > 0)).toBe(true);
      }
      await cdp.detach();
    });
  }

for (const surface of surfaces)
  for (const width of [1280, 375]) {
    test(`graph accessibility: ${surface}, ${width}px`, async ({
      page,
    }, info) => {
      await page.setViewportSize({ width, height: 900 });
      await mockGraphQuality(page, 1000);
      await installProbe(page);
      const { region } = await openSurface(page, surface, 1000, true);
      const control = selection(region, surface);
      const tabStops = await tabTo(page, control);
      await expect(control).toBeFocused();
      await page.keyboard.press("Enter");
      await detailVisible(region, surface);
      await expect(region.getByText(/배치 계산 중/)).toHaveCount(0);
      const next = await page.evaluate(() => document.activeElement?.tagName);
      await page.keyboard.press("Tab");
      expect(
        await page.evaluate(() => document.activeElement?.tagName),
      ).not.toBe("BODY");
      // Also scan the shared detail overlay; popup selection is fixture setup, not the keyboard claim.
      await region
        .getByRole("combobox", { name: /노드 찾기$/ })
        .selectOption({ index: 1 });
      await expect(
        region.getByRole("complementary", { name: "그래프 상세", exact: true }),
      ).toBeVisible();
      const overflow = await region.evaluate((node) => ({
        scroll: node.scrollWidth,
        client: node.clientWidth,
      }));
      expect.soft(overflow.scroll).toBeLessThanOrEqual(overflow.client + 1);
      const results = await new AxeBuilder({ page })
        .include(`section[aria-label="${labels[surface]}"]`)
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      const axePath = info.outputPath("axe.json");
      await writeFile(
        axePath,
        JSON.stringify(
          {
            surface,
            width,
            tabStops,
            activeTag: next,
            violations: results.violations,
          },
          null,
          2,
        ),
      );
      await info.attach("axe.json", {
        path: axePath,
        contentType: "application/json",
      });
      console.log(
        `GRAPH_AXE ${JSON.stringify({ surface, width, violations: results.violations.length, tabStops, overflow })}`,
      );
      expect
        .soft(
          results.violations.map((v) => ({
            id: v.id,
            impact: v.impact,
            targets: v.nodes.map((n) => n.target),
          })),
        )
        .toEqual([]);
      await region
        .locator(".react-flow")
        .evaluate((node) => node.scrollIntoView({ block: "center" }));
      await page.screenshot({
        path: info.outputPath(`${surface}-${width}.png`),
      });
    });
  }
