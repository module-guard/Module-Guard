import { expect, test, type Page, type Route } from "@playwright/test";
import type { Scan } from "../src/components/live-shared";
import { partialAnalysis, scanFixture } from "./fixtures/analysis-coverage";

// 실패한 검사는 실제 스택에서 만들 수 없어 API 응답을 모두 대신 돌려준다(#20).
const OLD = "11111111-1111-4111-8111-111111111111";
const NEW = "33333333-3333-4333-8333-333333333333";
const OTHER = "44444444-4444-4444-8444-444444444444";
const ORIGIN = "https://github.com/dev-user/net";
const repository = {
  owner: "dev-user",
  name: "net",
  version: "v1",
  visibility: "PUBLIC" as const,
};
const integrity = {
  integrity_status: "VERIFIED" as const,
  manifest_sha256: "a".repeat(64),
  snapshot_sha256: null,
  source_commit_sha: "c".repeat(40),
  module_path: ".",
  mismatched_files: [],
  download_allowed: false,
  artifact_integrity_status: "UNVERIFIED" as const,
};
const failedScan = (overrides: Partial<Scan> = {}) =>
  scanFixture({
    id: OLD,
    status: "FAILED",
    repository,
    partial_result: true,
    analysis: partialAnalysis,
    failed_stage: "persistence",
    error_code: "SnapshotError",
    error_message: "Source snapshot could not be stored.",
    integrity,
    ...overrides,
  });
const report = (overrides: Record<string, unknown> = {}) => ({
  upload_id: "u".repeat(32),
  origin_type: "GITHUB",
  origin_url: ORIGIN,
  commit_hash: "c".repeat(40),
  suggested_name: "net",
  license: null,
  file_count: 3,
  size_bytes: 2048,
  excluded: [],
  excluded_count: 0,
  warnings: [],
  expires_at: new Date(Date.now() + 3600_000).toISOString(),
  ...overrides,
});

type Reply = { status: number; json: unknown };
type Options = {
  login?: string | null;
  isOwner?: boolean;
  originType?: "GITHUB" | "UPLOAD";
  scan?: Scan;
  /** n번째(0부터) 재시도 요청에 줄 응답. 없으면 202. */
  retry?: (n: number) => Reply;
  prepare?: () => unknown;
};

async function mockApi(page: Page, options: Options = {}) {
  const {
    login = "dev-user",
    isOwner = true,
    originType = "GITHUB",
    retry,
    prepare = () => report(),
  } = options;
  let old = options.scan ?? failedScan();
  const calls = { retry: [] as unknown[], prepare: 0, newScan: 0 };
  const fulfill = (route: Route, json: unknown, status = 200) =>
    route.fulfill({
      status,
      json,
      headers: {
        "access-control-allow-origin": route.request().headers().origin ?? "*",
        "access-control-allow-credentials": "true",
        "access-control-allow-headers": "content-type",
        "access-control-allow-methods": "GET,POST,DELETE,OPTIONS",
      },
    });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    if (request.method() === "OPTIONS") return fulfill(route, {}, 204);
    const path = new URL(request.url()).pathname;
    if (path === "/api/me")
      return fulfill(route, {
        user: login ? { login, name: null, avatar_url: null } : null,
        login_available: true,
      });
    if (path === "/api/health")
      return fulfill(route, {
        status: "ok",
        database: "connected",
        checkov: "available",
      });
    if (path === `/api/scans/${OLD}`) return fulfill(route, old);
    if (path === `/api/scans/${NEW}`) {
      calls.newScan += 1;
      return fulfill(
        route,
        scanFixture({
          id: NEW,
          status: calls.newScan > 1 ? "COMPLETED" : "QUEUED",
          repository,
          retry_of: OLD,
          integrity,
        }),
      );
    }
    if (path === "/api/repositories/dev-user/net")
      return fulfill(route, {
        owner: "dev-user",
        name: "net",
        is_owner: isOwner,
        origin_type: originType,
        origin_url: originType === "GITHUB" ? ORIGIN : null,
      });
    if (path === "/api/uploads/github" || path === "/api/uploads/zip") {
      calls.prepare += 1;
      return fulfill(route, prepare());
    }
    if (path === `/api/scans/${OLD}/retry`) {
      expect(request.method()).toBe("POST");
      const n = calls.retry.length;
      calls.retry.push(request.postDataJSON());
      const reply = retry?.(n) ?? {
        status: 202,
        json: { scan_id: NEW, status: "QUEUED" },
      };
      if (reply.status === 202) old = { ...old, retried_by: NEW };
      return fulfill(route, reply.json, reply.status);
    }
    if (/\/(findings|correlations|entities)$/.test(path))
      return fulfill(route, []);
    throw new Error(`Unexpected API request: ${request.method()} ${path}`);
  });
  return {
    calls,
    alreadyRetried(by: string) {
      old = { ...old, retried_by: by };
    },
  };
}

const error = (status: number, code: string, message = "server message") => ({
  status,
  json: { error: { code, message } },
});

async function openFailed(page: Page) {
  await page.goto(`/@dev-user/net/scans/${OLD}`);
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
}

async function prepareSource(page: Page) {
  await page.getByRole("button", { name: "다시 시도하기" }).click();
  await page.getByRole("button", { name: "소스 준비" }).click();
  await expect(page.getByRole("region", { name: "확인 결과" })).toBeVisible();
}

test("failed → prepare the same source → retry → the new attempt", async ({
  page,
}) => {
  const api = await mockApi(page);
  await openFailed(page);
  // 부분 분석·무결성 표시는 그대로 남는다.
  await expect(page.getByRole("region", { name: "모듈 무결성" })).toContainText(
    "정상",
  );
  await expect(
    page.getByRole("region", { name: "분석 범위 안내" }),
  ).toContainText("부분 결과가 있더라도");

  await page.getByRole("button", { name: "다시 시도하기" }).click();
  // 재시도는 원래 GitHub 주소로만 준비한다.
  const url = page.getByLabel("GitHub 저장소 주소");
  await expect(url).toHaveValue(ORIGIN);
  await expect(url).toHaveAttribute("readonly", "");
  await expect(page.getByRole("tab", { name: "zip 파일" })).toHaveCount(0);
  await page.getByRole("button", { name: "소스 준비" }).click();
  await expect(page.getByRole("region", { name: "확인 결과" })).toContainText(
    "3개 파일",
  );

  // 두 번 눌러도 요청은 하나다.
  await page.getByRole("button", { name: "이 소스로 다시 시도" }).dblclick();
  await expect(page).toHaveURL(new RegExp(`/@dev-user/net/scans/${NEW}$`));
  expect(api.calls.retry).toEqual([
    { upload_id: "u".repeat(32), acknowledge_warnings: false },
  ]);
  await expect(page.getByText("완료", { exact: true }).first()).toBeVisible({
    timeout: 10000,
  });
  await page.getByRole("link", { name: "이전 시도" }).click();
  await expect(page).toHaveURL(new RegExp(`/scans/${OLD}$`));
  await expect(page.getByRole("link", { name: "다음 시도" })).toHaveAttribute(
    "href",
    `/@dev-user/net/scans/${NEW}`,
  );
  // 이미 다시 시도한 검사에는 실행 버튼이 없다.
  await expect(page.getByRole("button", { name: "다시 시도하기" })).toHaveCount(
    0,
  );
});

test("an uploaded module retries from a zip and confirms warnings first", async ({
  page,
}) => {
  const api = await mockApi(page, {
    originType: "UPLOAD",
    prepare: () =>
      report({
        origin_type: "UPLOAD",
        origin_url: null,
        warnings: [
          {
            file: "main.tf",
            line: 3,
            rule: "entropy",
            title: "High entropy",
            preview: "abcd****",
          },
        ],
      }),
  });
  await openFailed(page);
  await page.getByRole("button", { name: "다시 시도하기" }).click();
  await expect(page.getByRole("tab", { name: "GitHub 주소" })).toHaveCount(0);
  await page.locator("#source-zip").setInputFiles({
    name: "net.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("PK\u0005\u0006" + "\u0000".repeat(18), "binary"),
  });
  await page.getByRole("button", { name: "소스 준비" }).click();
  const submit = page.getByRole("button", { name: "이 소스로 다시 시도" });
  await expect(submit).toBeDisabled();
  await page.getByLabel("비밀값이 아님을 확인했습니다").check();
  await submit.click();
  await expect(page).toHaveURL(new RegExp(`/scans/${NEW}$`));
  expect(api.calls.retry).toEqual([
    { upload_id: "u".repeat(32), acknowledge_warnings: true },
  ]);
});

for (const viewer of [
  { name: "a signed-out viewer", login: null, isOwner: false },
  { name: "another user", login: "someone-else", isOwner: false },
  // 이름이 같아도 서버가 소유자가 아니라고 하면 보이지 않는다.
  { name: "a non-owner by the server", login: "dev-user", isOwner: false },
]) {
  test(`no retry controls for ${viewer.name}`, async ({ page }) => {
    const api = await mockApi(page, viewer);
    await openFailed(page);
    await expect(page.getByRole("alert").first()).toBeVisible();
    await expect(
      page.getByRole("button", { name: "다시 시도하기" }),
    ).toHaveCount(0);
    expect(api.calls.retry).toHaveLength(0);
  });
}

test("an expired source sends the owner back to prepare it again", async ({
  page,
}) => {
  const api = await mockApi(page, {
    retry: (n) =>
      n === 0
        ? error(404, "StagedUploadNotFoundError")
        : { status: 202, json: { scan_id: NEW, status: "QUEUED" } },
  });
  await openFailed(page);
  await prepareSource(page);
  await page.getByRole("button", { name: "이 소스로 다시 시도" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "만료" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "소스 준비" })).toBeVisible();
  await page.getByRole("button", { name: "소스 준비" }).click();
  await page.getByRole("button", { name: "이 소스로 다시 시도" }).click();
  await expect(page).toHaveURL(new RegExp(`/scans/${NEW}$`));
  expect(api.calls.prepare).toBe(2);
});

test("a source that expired in the browser is not submitted", async ({
  page,
}) => {
  const api = await mockApi(page, {
    prepare: () => report({ expires_at: "2000-01-01T00:00:00Z" }),
  });
  await openFailed(page);
  await prepareSource(page);
  await page.getByRole("button", { name: "이 소스로 다시 시도" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "만료" }),
  ).toBeVisible();
  expect(api.calls.retry).toHaveLength(0);
});

test("a mismatched source asks for the original one", async ({ page }) => {
  await mockApi(page, { retry: () => error(422, "SourceMismatch") });
  await openFailed(page);
  await prepareSource(page);
  await page.getByRole("button", { name: "이 소스로 다시 시도" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "원래 등록한 출처" }),
  ).toBeVisible();
  await expect(page.getByLabel("GitHub 저장소 주소")).toHaveValue(ORIGIN);
});

test("a full queue keeps the prepared source for another press", async ({
  page,
}) => {
  const api = await mockApi(page, {
    retry: (n) =>
      n === 0
        ? error(503, "ScanQueueFullError")
        : { status: 202, json: { scan_id: NEW, status: "QUEUED" } },
  });
  await openFailed(page);
  await prepareSource(page);
  const submit = page.getByRole("button", { name: "이 소스로 다시 시도" });
  await submit.click();
  await expect(
    page.getByRole("alert").filter({ hasText: "대기열이 가득" }),
  ).toBeVisible();
  await submit.click();
  await expect(page).toHaveURL(new RegExp(`/scans/${NEW}$`));
  expect(api.calls.prepare).toBe(1);
  expect(api.calls.retry).toHaveLength(2);
});

test("a rate limit is explained without losing the source", async ({
  page,
}) => {
  await mockApi(page, { retry: () => error(429, "RateLimitedError") });
  await openFailed(page);
  await prepareSource(page);
  await page.getByRole("button", { name: "이 소스로 다시 시도" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "진행 중인 올리기가 많습니다" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "이 소스로 다시 시도" }),
  ).toBeEnabled();
});

test("an already retried scan points to the existing attempt", async ({
  page,
}) => {
  let api: Awaited<ReturnType<typeof mockApi>>;
  api = await mockApi(page, {
    retry: () => {
      // 다른 탭에서 먼저 다시 시도했다.
      api.alreadyRetried(OTHER);
      return error(409, "ConflictError", "이미 다시 시도된 검사입니다.");
    },
  });
  await openFailed(page);
  await prepareSource(page);
  await page.getByRole("button", { name: "이 소스로 다시 시도" }).click();
  const alert = page.getByRole("alert").filter({ hasText: "이미 다시 시도된" });
  await expect(alert).toBeVisible();
  await expect(
    alert.getByRole("link", { name: "이미 시작된 새 시도 보기" }),
  ).toHaveAttribute("href", `/@dev-user/net/scans/${OTHER}`);
  await expect(
    page.getByRole("button", { name: "이 소스로 다시 시도" }),
  ).toHaveCount(0);
});

test("a changed source and an integrity mismatch point to a new version", async ({
  page,
}) => {
  await mockApi(page, { retry: () => error(422, "SourceChanged") });
  await openFailed(page);
  await prepareSource(page);
  await page.getByRole("button", { name: "이 소스로 다시 시도" }).click();
  await expect(
    page.getByRole("link", { name: "새 버전으로 올리기" }),
  ).toHaveAttribute("href", "/new?repository=net");

  await page.unrouteAll();
  await mockApi(page, {
    scan: failedScan({
      integrity: { ...integrity, integrity_status: "MISMATCH" },
    }),
  });
  await openFailed(page);
  await expect(
    page.getByText("무결성 불일치가 확인된 버전은 다시 시도할 수 없습니다."),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "다시 시도하기" })).toHaveCount(
    0,
  );
});
