import { expect, test } from "@playwright/test";
import type { components } from "../src/api/generated/schema";
import { scanFixture } from "./fixtures/analysis-coverage";

type Card = components["schemas"]["RepositoryCard"];
const id = "11111111-1111-4111-8111-111111111111";
const card: Card = {
  owner: "bob",
  name: "net",
  mirror: false,
  owner_avatar_url: null,
  description: "Network module",
  provider: null,
  visibility: "PUBLIC",
  origin_type: "FORK",
  forked_from: null,
  star_count: 0,
  fork_count: 0,
  updated_at: "2026-11-01T00:00:00Z",
  state: "PUBLISHED",
  failure: null,
  badge: {
    version: "v1",
    critical_rules: 1,
    high_rules: 0,
    unknown: 0,
    total: 1,
    scanned_at: null,
    scan_status: "COMPLETED",
    partial_result: true,
    scan_id: id,
  },
};

for (const scenario of [
  { status: "COMPLETED", partial: false, critical: 0, label: "주요 위험 없음" },
  { status: "COMPLETED", partial: true, critical: 0, label: "부분 분석" },
  {
    status: "COMPLETED",
    partial: true,
    critical: 1,
    label: "위험 1종 · 부분 분석",
  },
  { status: "FAILED", partial: false, critical: 0, label: "분석 실패" },
  { status: null, partial: false, critical: 0, label: "미검사" },
] as const) {
  test(`consistent badge across home, explore, profile and module: ${scenario.label}`, async ({
    page,
  }) => {
    const scan = scenario.status
      ? scanFixture({
          id,
          status: scenario.status,
          partial_result: scenario.partial,
        })
      : null;
    const current: Card = {
      ...card,
      badge: {
        ...card.badge!,
        critical_rules: scenario.critical,
        scan_status: scenario.status,
        partial_result: scenario.partial,
        scan_id: scan ? id : null,
      },
    };
    const version = {
      version: "v1",
      state: "STORED",
      scan,
      created_at: card.updated_at,
      critical_rules: scenario.critical,
      high_rules: 0,
      readme: "# Network",
      variables: [],
      outputs: [],
      integrity: {
        integrity_status: "UNVERIFIED",
        mismatched_files: [],
        download_allowed: false,
      },
    };
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let json: unknown;
      if (path === "/api/me")
        json = {
          user: { login: "bob", name: null, avatar_url: null },
          login_available: true,
        };
      else if (
        path === "/api/repositories" ||
        path === "/api/users/bob/repositories"
      )
        json = [current];
      else if (path === "/api/repository-providers") json = [];
      else if (path === "/api/me/home")
        json = {
          repositories: [current],
          attention: [],
          activity: [],
          severity: scanFixture().summary,
          stale_after_days: 30,
        };
      else if (path === "/api/users/bob")
        json = {
          login: "bob",
          name: null,
          avatar_url: null,
          is_self: true,
          repository_count: 1,
          fork_count: 0,
          star_count: 0,
        };
      else if (path === "/api/repositories/bob/net")
        json = {
          ...current,
          is_owner: true,
          starred: false,
          latest_version: "v1",
          version,
          versions: [version],
        };
      else if (path.endsWith("/integrity")) json = version.integrity;
      else if (path === `/api/scans/${id}`) json = scan;
      else json = [];
      await route.fulfill({
        json,
        headers: {
          "access-control-allow-origin":
            route.request().headers().origin ?? "*",
          "access-control-allow-credentials": "true",
        },
      });
    });
    for (const path of ["/home", "/explore", "/@bob", "/@bob/net"]) {
      await page.goto(path);
      if (scan) {
        const link = page.getByRole("link", {
          name: `${scenario.label} · 검사 상세 보기`,
          exact: true,
        });
        await expect(link).toBeVisible();
        await expect(link).toHaveAttribute("href", `/@bob/net/scans/${id}`);
      } else {
        await expect(page.getByText("미검사", { exact: true })).toBeVisible();
        await expect(page.locator('a[href*="/scans/"]')).toHaveCount(0);
      }
      if (scenario.label !== "주요 위험 없음")
        await expect(
          page.getByText("주요 위험 없음", { exact: true }),
        ).toHaveCount(0);
    }
    await page.goto("/explore");
    await page.getByRole("link", { name: "net", exact: true }).click();
    await expect(page).toHaveURL(/\/@bob\/net$/);
    if (scan) {
      await page.goto("/explore");
      await page
        .getByRole("link", {
          name: `${scenario.label} · 검사 상세 보기`,
          exact: true,
        })
        .click();
      await expect(page).toHaveURL(new RegExp(`/@bob/net/scans/${id}$`));
    }
  });
}
