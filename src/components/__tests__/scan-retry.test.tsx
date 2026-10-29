import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { scanFixture } from "../../../e2e/fixtures/analysis-coverage";
import { ScanRetry } from "../scan-retry";
const state = vi.hoisted(() => ({ user: null as null | { login: string } }));
vi.mock("../auth", () => ({ useAuth: () => ({ user: state.user }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../publish", () => ({ Source: () => null }));
const failed = () =>
  scanFixture({
    status: "FAILED",
    repository: {
      owner: "Owner",
      name: "repo",
      version: "v1",
      visibility: "PRIVATE",
    },
  });
describe("retry access and history", () => {
  beforeEach(() => {
    state.user = null;
  });
  it("hides retry for anonymous users and non-owners", () => {
    expect(renderToStaticMarkup(<ScanRetry scan={failed()} />)).toBe("");
    state.user = { login: "other" };
    expect(renderToStaticMarkup(<ScanRetry scan={failed()} />)).toBe("");
  });
  it("offers retry only for the owner's failed repository scan", () => {
    state.user = { login: "owner" };
    expect(renderToStaticMarkup(<ScanRetry scan={failed()} />)).toContain(
      "실패한 검사 다시 시도",
    );
    expect(
      renderToStaticMarkup(
        <ScanRetry scan={{ ...failed(), status: "COMPLETED" }} />,
      ),
    ).toBe("");
    expect(
      renderToStaticMarkup(
        <ScanRetry scan={{ ...failed(), repository: null }} />,
      ),
    ).toBe("");
  });
  it("keeps history navigation and hides retry when a next attempt already exists", () => {
    state.user = { login: "owner" };
    const html = renderToStaticMarkup(
      <ScanRetry
        scan={{ ...failed(), retry_of: "previous", retried_by: "next" }}
      />,
    );
    expect(html).toContain('href="/scans/previous"');
    expect(html).toContain('href="/scans/next"');
    expect(html).not.toContain("실패한 검사 다시 시도");
  });
});
