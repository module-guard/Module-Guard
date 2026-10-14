import { afterEach, describe, expect, it, vi } from "vitest";
import { api, unwrap } from "../client";
import type { components } from "../generated/schema";
import schema from "../../../openapi.json";
const id = "11111111-1111-4111-8111-111111111111";
afterEach(() => vi.unstubAllGlobals());
function respond(data: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(data)),
  );
}
describe("OpenAPI client", () => {
  it("creates scans with the generated request and response", async () => {
    const response: components["schemas"]["ScanAccepted"] = {
      scan_id: id,
      status: "QUEUED",
    };
    respond(response);
    expect(
      await unwrap(
        api.POST("/api/scans", {
          body: { source: "https://github.com/org/repo" },
        }),
      ),
    ).toEqual(response);
    const request = vi.mocked(fetch).mock.calls[0][0] as Request;
    expect(request.method).toBe("POST");
    expect(await request.json()).toEqual({
      source: "https://github.com/org/repo",
    });
  });
  it("preserves scan statuses and analysis", async () => {
    const module: components["schemas"]["ModuleResponse"] = {
      id,
      name: "repo",
      source_url: "https://github.com/org/repo",
      source_type: "GITHUB",
      repository_owner: "org",
      repository_name: "repo",
      default_branch: null,
      provider: null,
      latest_version: null,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      is_demo: false,
    };
    const scan: components["schemas"]["ScanResponse"] = {
      id,
      module_id: id,
      module,
      status: "SCANNING",
      scanner: "checkov",
      commit_hash: null,
      branch: null,
      summary: {
        total: 0,
        critical: 0,
        high: 0,
        medium: 0,
        low: 0,
        info: 0,
        unknown: 0,
      },
      partial_result: false,
      warnings: [],
      error_message: null,
      started_at: null,
      finished_at: null,
      duration_ms: null,
      created_at: module.created_at,
      analysis: {
        schema_version: 1,
        entities: 0,
        edges: 0,
        dependency_edges: 0,
        modules: 0,
        external_modules: 0,
        providers: 0,
        source_trust_findings: 0,
        unresolved_references: 0,
        files_parsed: 0,
        files_failed: 0,
        max_observed_module_depth: 0,
        external_transitive_depth_unknown: false,
        stages: { checkov: { status: "RUNNING" } },
      },
    };
    respond(scan);
    expect(
      (
        await unwrap(
          api.GET("/api/scans/{scan_id}", {
            params: { path: { scan_id: id } },
          }),
        )
      ).analysis?.stages?.checkov.status,
    ).toBe("RUNNING");
  });
  it("parses findings with scanner query", async () => {
    const data: components["schemas"]["FindingResponse"][] = [];
    respond(data);
    expect(
      await unwrap(
        api.GET("/api/scans/{scan_id}/findings", {
          params: {
            path: { scan_id: id },
            query: { scanner: "source_trust", severity: "HIGH" },
          },
        }),
      ),
    ).toEqual([]);
    expect((vi.mocked(fetch).mock.calls[0][0] as Request).url).toContain(
      "scanner=source_trust",
    );
  });
  it("parses dependencies", async () => {
    const data: components["schemas"]["DependencyResponse"] = {
      modules: [],
      providers: [],
    };
    respond(data);
    expect(
      await unwrap(
        api.GET("/api/scans/{scan_id}/dependencies", {
          params: { path: { scan_id: id } },
        }),
      ),
    ).toEqual(data);
  });
  it("parses graph", async () => {
    const data: components["schemas"]["GraphResponse"] = {
      schema_version: 1,
      nodes: [],
      edges: [],
    };
    respond(data);
    expect(
      await unwrap(
        api.GET("/api/scans/{scan_id}/graph", {
          params: { path: { scan_id: id } },
        }),
      ),
    ).toEqual(data);
  });
  it("parses flows and preserves pagination", async () => {
    const data: components["schemas"]["FlowResponse"] = {
      nodes: [],
      edges: [],
      summary: { total_flows: 0 },
      limit: 20,
      offset: 0,
    };
    respond(data);
    expect(
      await unwrap(
        api.GET("/api/scans/{scan_id}/flows", {
          params: {
            path: { scan_id: id },
            query: { source: "module.a.output", offset: 0 },
          },
        }),
      ),
    ).toEqual(data);
  });
  it("parses correlation list", async () => {
    const data: components["schemas"]["FindingResponse"][] = [];
    respond(data);
    expect(
      await unwrap(
        api.GET("/api/scans/{scan_id}/correlations", {
          params: { path: { scan_id: id } },
        }),
      ),
    ).toEqual(data);
  });
  it("reports backend and network errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { error: { message: "No Terraform files found." } },
          { status: 400 },
        ),
      ),
    );
    await expect(
      unwrap(api.POST("/api/scans", { body: { source: "bad" } })),
    ).rejects.toThrow("No Terraform");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("offline");
      }),
    );
    await expect(unwrap(api.GET("/api/health"))).rejects.toThrow(
      "백엔드에 연결",
    );
  });
  it("keeps all frontend feature contracts in exported OpenAPI", () => {
    for (const route of [
      "/api/scans",
      "/api/scans/{scan_id}",
      "/api/scans/{scan_id}/flows",
      "/api/scans/{scan_id}/graph",
      "/api/scans/{scan_id}/correlations",
      "/api/dev/demo/correlation",
      "/api/me/home",
      "/api/repositories",
      "/api/repositories/{owner}/{name}",
      "/api/repositories/{owner}/{name}/usage",
    ])
      expect(schema.paths).toHaveProperty(route);
    // 사이드바 시절 화면이 쓰던 API는 남아 있지 않다.
    for (const route of [
      "/api/dashboard",
      "/api/modules",
      "/api/modules/{module_id}",
    ])
      expect(schema.paths).not.toHaveProperty(route);
    expect(schema.paths["/api/scans"]).not.toHaveProperty("get");
  });
});

it("cancels requests and enforces request timeout", async () => {
  vi.useFakeTimers();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (request: Request) =>
        new Promise<Response>((_, reject) => {
          const fail = () => reject(new DOMException("Aborted", "AbortError"));
          if (request.signal.aborted) fail();
          else request.signal.addEventListener("abort", fail, { once: true });
        }),
    ),
  );
  try {
    const controller = new AbortController();
    const pending = unwrap(
      api.GET("/api/health", { signal: controller.signal }),
    );
    const assertion = expect(pending).rejects.toThrow("취소");
    controller.abort();
    await assertion;
    const timed = unwrap(api.GET("/api/health"));
    const timeoutAssertion = expect(timed).rejects.toThrow("초과");
    await vi.advanceTimersByTimeAsync(30001);
    await timeoutAssertion;
  } finally {
    vi.useRealTimers();
  }
});

it("explains pipeline failures without requiring console access", async () => {
  const { explainScanError } = await import("../errors");
  expect(
    explainScanError("No Terraform files found in the repository."),
  ).toContain("Terraform 파일이 없습니다");
  expect(explainScanError("Repository clone failed.")).toContain("수집에 실패");
  expect(explainScanError("Process timed out")).toContain("제한 시간");
  expect(explainScanError("Checkov binary is not installed.")).toContain(
    "시스템 상태",
  );
});
