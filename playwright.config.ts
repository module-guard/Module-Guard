import { defineConfig, devices } from "@playwright/test";

// 기본값: e2e 전용 API·DB(docker-compose.e2e.yml)와 전용 프런트 빌드를 띄워 실제 DB와 분리한다.
// PLAYWRIGHT_BASE_URL을 주면(CI 등) 이미 떠 있는 스택을 그대로 쓴다.
const external = process.env.PLAYWRIGHT_BASE_URL;
const apiUrl = "http://localhost:8001";
if (!external) process.env.PLAYWRIGHT_API_URL ??= apiUrl;

export default defineConfig({
  testDir: "./e2e",
  timeout: 60000,
  fullyParallel: false,
  workers: 1,
  globalSetup: external ? undefined : "./e2e/global-setup.ts",
  webServer: external
    ? undefined
    : {
        // 배포와 같은 프로덕션 빌드로 돌린다(개발 서버의 첫 컴파일 지연이 없다).
        command: "npx next build && npx next start -p 3100",
        url: "http://localhost:3100",
        reuseExistingServer: false,
        timeout: 300000,
        env: {
          NEXT_PUBLIC_API_BASE_URL: apiUrl,
          NEXT_PUBLIC_ENABLE_TEST_UI: "true",
          NEXT_DIST_DIR: ".next-e2e",
          // next start는 standalone 출력과 같이 쓸 수 없다.
          NEXT_OUTPUT: "server",
        },
      },
  use: {
    baseURL: external || "http://localhost:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
