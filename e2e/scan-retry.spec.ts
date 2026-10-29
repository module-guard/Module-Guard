import { expect, test, type Page } from "@playwright/test";
import { scanFixture } from "./fixtures/analysis-coverage";

const oldId = "11111111-1111-4111-8111-111111111111";
const newId = "22222222-2222-4222-8222-222222222222";
async function setup(
  page: Page,
  options: {
    owner?: boolean;
    status?: number;
    code?: string;
    conflict?: boolean;
    warnings?: boolean;
  } = {},
) {
  let posts = 0;
  let retried = false;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const headers = {
      "access-control-allow-origin": request.headers().origin || "*",
      "access-control-allow-credentials": "true",
      "access-control-allow-headers": "content-type",
      "access-control-allow-methods": "GET, POST, OPTIONS",
    };
    if (request.method() === "OPTIONS")
      return route.fulfill({ status: 204, headers });
    let json: unknown;
    let status = 200;
    if (path === "/api/me")
      json = {
        user: {
          id: "owner-id",
          login: options.owner === false ? "other" : "owner",
          name: "Owner",
          avatar_url: null,
        },
        login_available: true,
      };
    else if (path === "/api/health")
      json = { status: "ok", database: "connected", checkov: "available" };
    else if (path === "/api/uploads/github")
      json = {
        upload_id: "prepared",
        origin_type: "GITHUB",
        origin_url: "https://github.com/org/repo",
        suggested_name: "repo",
        commit_hash: "a".repeat(40),
        file_count: 1,
        size_bytes: 100,
        excluded: [],
        excluded_count: 0,
        warnings: options.warnings
          ? [{ file: "main.tf", line: 1, preview: "example" }]
          : [],
        expires_at: "2099-01-01T00:00:00Z",
      };
    else if (path.endsWith("/retry")) {
      posts++;
      expect(request.postDataJSON()).toEqual({
        upload_id: "prepared",
        acknowledge_warnings: !!options.warnings,
      });
      status = options.status || 202;
      retried = status === 202 || !!options.conflict;
      json =
        status === 202
          ? { scan_id: newId, status: "QUEUED" }
          : {
              error: {
                code: options.code || "ConflictError",
                message: "failure",
              },
            };
    } else if (
      path === `/api/scans/${oldId}` ||
      path === `/api/scans/${newId}`
    ) {
      const isNew = path.endsWith(newId);
      json = scanFixture({
        id: isNew ? newId : oldId,
        status: isNew ? "COMPLETED" : "FAILED",
        repository: {
          owner: "owner",
          name: "repo",
          version: "v1",
          visibility: "PRIVATE",
        },
        module: {
          ...scanFixture().module,
          source_url: "https://github.com/org/repo",
        },
        retry_of: isNew ? oldId : null,
        retried_by: !isNew && retried ? newId : null,
      });
    } else
      throw new Error(`Unexpected API request ${request.method()} ${path}`);
    await route.fulfill({ status, json, headers });
  });
  await page.goto(`/scans/${oldId}`);
  return () => posts;
}
async function prepare(page: Page) {
  await page.getByRole("button", { name: "실패한 검사 다시 시도" }).click();
  await page.getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.getByText(/1개 파일 준비 완료/)).toBeVisible();
}

test("owner prepares source, acknowledges warnings and follows retry history", async ({
  page,
}) => {
  const posts = await setup(page, { warnings: true });
  await prepare(page);
  await expect(
    page.getByRole("button", { name: "검사 다시 시작" }),
  ).toBeDisabled();
  await page
    .getByRole("checkbox", { name: "비밀값이 아님을 확인했습니다" })
    .check();
  await page.getByRole("button", { name: "검사 다시 시작" }).click();
  await expect(page).toHaveURL(new RegExp(`/scans/${newId}$`));
  await expect(
    page.getByRole("link", { name: "이전 검사 보기" }),
  ).toHaveAttribute("href", `/scans/${oldId}`);
  expect(posts()).toBe(1);
});
test("non-owner cannot start retry", async ({ page }) => {
  await setup(page, { owner: false });
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "실패한 검사 다시 시도" }),
  ).toHaveCount(0);
});
for (const [status, code, message] of [
  [
    404,
    "StagedUploadNotFoundError",
    "준비한 소스가 만료되었거나 사용되었습니다",
  ],
  [422, "SourceMismatch", "원래 등록한 출처와 다릅니다"],
  [422, "SourceChanged", "등록 당시와 내용이 달라졌습니다"],
  [429, "RateLimitedError", "진행 중인 검사가 많습니다"],
  [503, "ScanQueueFullError", "검사 대기열이 가득 찼습니다"],
  [403, "ForbiddenOriginError", "로그인 또는 접근 권한을 확인"],
] as const) {
  test(`retry handles ${code}`, async ({ page }) => {
    await setup(page, { status, code });
    await prepare(page);
    await page.getByRole("button", { name: "검사 다시 시작" }).click();
    await expect(page.getByRole("alert")).toContainText(message);
    if (code === "SourceChanged")
      await expect(
        page.getByRole("link", { name: "새 버전 올리기" }),
      ).toHaveAttribute("href", "/new?repository=repo");
    if (code === "SourceMismatch" || code === "StagedUploadNotFoundError")
      await expect(
        page.getByRole("button", { name: "확인", exact: true }),
      ).toBeVisible();
  });
}
test("conflict refreshes the accessible next attempt", async ({ page }) => {
  const posts = await setup(page, { status: 409, conflict: true });
  await prepare(page);
  await page.getByRole("button", { name: "검사 다시 시작" }).click();
  await expect(
    page.getByRole("link", { name: "다음 검사 보기" }),
  ).toHaveAttribute("href", `/scans/${newId}`);
  await expect(
    page.getByRole("button", { name: "검사 다시 시작" }),
  ).toHaveCount(0);
  expect(posts()).toBe(1);
});
