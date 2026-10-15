import { test, expect } from "@playwright/test";
// The e2e stack points GitHub login at the development fake provider, so the real
// login, callback, state and cookie code runs without leaving localhost.
test("GitHub login → avatar menu → logout", async ({ page, context }) => {
  await page.goto("/explore");
  // The header button; /home also shows one in its body when signed out.
  const login = page
    .getByRole("banner")
    .getByRole("link", { name: "GitHub로 로그인" });
  await expect(login).toBeVisible();
  await login.click();
  // Back on the page the login started from, now signed in.
  await expect(page).toHaveURL(/\/explore$/);
  const account = page.getByRole("button", { name: "계정 메뉴: dev-user" });
  await expect(account).toBeVisible();
  await expect(login).toHaveCount(0);

  const cookies = await context.cookies();
  const session = cookies.find((c) => c.name === "mg_session");
  expect(session).toMatchObject({ httpOnly: true, sameSite: "Lax", path: "/" });
  // The one-time state cookie is gone, and scripts cannot read the session.
  expect(cookies.some((c) => c.name === "mg_oauth_state")).toBe(false);
  expect(await page.evaluate(() => document.cookie)).not.toContain(
    "mg_session",
  );

  await page.reload();
  await expect(account).toBeVisible();
  await page.goto("/home");
  await expect(page.getByRole("heading", { name: "내 홈" })).toBeVisible();
  await expect(account).toBeVisible();

  await account.click();
  await expect(page.getByText("@dev-user")).toBeVisible();
  await page.getByRole("menuitem", { name: "로그아웃" }).click();
  await expect(login).toBeVisible();
  await expect(account).toHaveCount(0);
  expect((await context.cookies()).some((c) => c.name === "mg_session")).toBe(
    false,
  );
  await page.reload();
  await expect(login).toBeVisible();
});

test("a forged login callback is rejected with a visible message", async ({
  page,
}) => {
  const api = process.env.PLAYWRIGHT_API_URL || "http://localhost:8000";
  await page.goto(`${api}/api/auth/github/callback?code=dev-code&state=forged`);
  await expect(page).toHaveURL(/\/home$/);
  await expect(
    page.getByRole("alert").filter({ hasText: "로그인" }),
  ).toContainText("로그인 요청이 만료되었거나 올바르지 않습니다");
  await expect(
    page.getByRole("banner").getByRole("link", { name: "GitHub로 로그인" }),
  ).toBeVisible();
});

test("login button is replaced by a notice when login is not configured", async ({
  page,
}) => {
  await page.route("**/api/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ user: null, login_available: false }),
    }),
  );
  await page.goto("/explore");
  await expect(page.getByText("로그인을 사용할 수 없습니다")).toBeVisible();
  await expect(page.getByRole("link", { name: "GitHub로 로그인" })).toHaveCount(
    0,
  );
});

test("sidebar-era addresses redirect for now (307) and old screens are gone", async ({
  page,
  request,
}) => {
  for (const [from, to] of [
    ["/dashboard", "/home"],
    ["/history", "/home"],
    ["/modules", "/explore"],
    ["/modules/0216af94-ca42-43a1-92d0-316e56341a08", "/explore"],
    ["/trusted", "/explore"],
  ]) {
    const response = await request.get(from, { maxRedirects: 0 });
    expect(response.status(), from).toBe(307);
    expect(new URL(response.headers().location, "http://x").pathname).toBe(to);
  }
  // Signed out, /home asks to log in instead of showing anything.
  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/home$/);
  await expect(page.getByRole("heading", { name: "내 홈" })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "GitHub로 로그인" }).last(),
  ).toBeVisible();
  const nav = page.getByRole("navigation", { name: "주 메뉴" }).first();
  for (const gone of ["대시보드", "모듈", "검사 기록", "신뢰 모듈"])
    await expect(
      nav.getByRole("link", { name: gone, exact: true }),
    ).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "탐색" })).toBeVisible();
  // An unknown old scan address is a not-found message, not a crash.
  await page.goto("/scans/00000000-0000-0000-0000-000000000000");
  await expect(page.getByText("검사를 찾을 수 없습니다")).toBeVisible();
});
