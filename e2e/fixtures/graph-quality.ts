import type { Page } from "@playwright/test";
import type { components } from "../../src/api/generated/schema";
import dynamicFixture from "../../backend/app/dynamic/fixtures/success.json";
import { scanFixture } from "./analysis-coverage";

type Schema = components["schemas"];
export const sizes = [20, 200, 1000] as const;
export const surfaces = ["lineage", "comparison", "risk", "dynamic"] as const;
export type Surface = (typeof surfaces)[number];
export const scanId = "11111111-1111-4111-8111-111111111111";
const date = "2026-11-12T00:00:00Z";
const uuid = (i: number) =>
  `00000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`;
const address = (i: number) => `module.network.aws_instance.node_${i}`;

export async function mockGraphQuality(page: Page, size: number) {
  const requests: { path: string; offset: number; returned: number }[] = [];
  const scan = scanFixture({
    repository: {
      owner: "quality",
      name: "fixture",
      version: "v1",
      visibility: "PUBLIC",
    },
  });
  const version = {
    version: "v1",
    state: "STORED",
    scan,
    created_at: date,
    critical_rules: 0,
    high_rules: 0,
    readme: "# Synthetic graph quality fixture",
    variables: [],
    outputs: [],
    integrity: {
      integrity_status: "UNVERIFIED",
      mismatched_files: [],
      download_allowed: false,
    },
  };
  const root: Schema["LineageNode"] = {
    ref: { id: uuid(0), owner: "quality", name: "fixture", version: "v1" },
    relation: "ORIGINAL",
    parent: null,
    comparison: {
      status: "NOT_COMPARABLE",
      reason: "NO_VISIBLE_PARENT",
      added: 0,
      removed: 0,
      modified: 0,
      unchanged: 0,
    },
    manifest_sha256: "a".repeat(64),
    file_count: 1,
    source: { url: null, commit_hash: "c".repeat(40), module_path: "." },
    publisher: { id: uuid(5000), login: "quality", system: false },
    published_at: date,
    declaration_version: "publisher-declaration/1",
    backfilled: false,
    author_asserted: false,
  };
  const lineage = Array.from({ length: size }, (_, i) => ({
    ...root,
    ref: { ...root.ref, id: uuid(i + 1), name: `module-${i + 1}` },
    relation: "FORK" as const,
    parent: root.ref,
    comparison: {
      ...root.comparison,
      status: "IDENTICAL" as const,
      reason: null,
    },
  }));
  const changes: Schema["Change"][] = Array.from({ length: size }, (_, i) => ({
    key: `resource-${i}`,
    kind: "resource",
    change: i % 2 ? "modified" : "added",
    before: null,
    after: {
      file: "modules/network/main.tf",
      line: i + 1,
      address: address(i),
      severity: "HIGH",
      rule: "CKV_AWS_24",
    },
  }));
  const riskPath = (i: number): Schema["RiskPath"] => {
    const count = Math.min(size, 201);
    return {
      finding_id: uuid(i),
      rule: `CKV_TEST_${i}`,
      severity: "HIGH",
      location: {
        file: "modules/network/main.tf",
        line: i + 1,
        address: address(i),
      },
      recommendation: "공개 연결과 권한을 검토하세요.",
      nodes: Array.from({ length: count }, (_, n) => ({
        id: `risk_${i}_${n}`,
        address: n ? `local.value_${n}` : address(i),
        type: n ? "LOCAL" : "RESOURCE",
        file_path: "modules/network/main.tf",
        line_start: n + 1,
      })),
      edges: Array.from({ length: count - 1 }, (_, n) => ({
        id: `edge_${i}_${n}`,
        source: `risk_${i}_0`,
        target: `risk_${i}_${n + 1}`,
        type: "REFERENCES",
        basis: "static_adjacent",
        resolved: true,
      })),
      truncated: size > 201,
      interpretation: "정적 추론이며 실제 실행·통신을 관측한 결과가 아닙니다.",
    };
  };
  const baseRun = dynamicFixture as Schema["DynamicRun"];
  const run: Schema["DynamicRun"] = {
    ...baseRun,
    stages: Array.from({ length: size }, (_, i) => ({
      ...baseRun.stages[2],
      id: `stage_${i}`,
      evidence_ids: [`evidence_${i}`],
    })),
    nodes: Array.from({ length: size }, (_, i) => ({
      id: `node_${i}`,
      resource_type: "fixture_resource",
      evidence_ids: [`evidence_${i}`],
    })),
    edges: Array.from({ length: size - 1 }, (_, i) => ({
      id: `link_${i}`,
      source: `node_${Math.floor(i / 4)}`,
      target: `node_${i + 1}`,
      relation: "simulated_dependency",
      evidence_ids: [`evidence_${i}`],
    })),
    evidence: Array.from({ length: size }, (_, i) => ({
      id: `evidence_${i}`,
      code: "SIMULATED_RESOURCE",
      redacted: true,
    })),
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname;
    if (request.method() !== "GET")
      throw new Error(`Unexpected mutation: ${request.method()} ${path}`);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    let data: unknown;
    if (path === "/api/me") data = { user: null, login_available: false };
    else if (path === "/api/health")
      data = { status: "ok", database: "connected", checkov: "available" };
    else if (path === "/api/repositories/quality/fixture")
      data = {
        owner: "quality",
        name: "fixture",
        mirror: false,
        is_owner: false,
        starred: false,
        visibility: "PUBLIC",
        description: "Synthetic performance and accessibility fixture",
        provider: null,
        origin_type: "UPLOAD",
        origin_url: null,
        forked_from: null,
        star_count: 0,
        fork_count: 0,
        updated_at: date,
        latest_version: "v1",
        version,
        versions: [{ ...version, version: "v0" }, version],
      };
    else if (path === `/api/scans/${scanId}`) data = scan;
    else if (path.endsWith("/integrity")) data = version.integrity;
    else if (path.endsWith("/lineage")) data = root;
    else if (path.endsWith("/lineage/graph")) {
      const limit = Number(url.searchParams.get("limit") ?? 20);
      const descendants = url.searchParams.get("direction") === "descendants";
      const nodes = descendants ? lineage.slice(offset, offset + limit) : [];
      requests.push({ path, offset, returned: nodes.length });
      data = {
        root: root.ref,
        direction: descendants ? "descendants" : "ancestors",
        nodes,
        edges: nodes.map((n) => ({
          parent: root.ref.id,
          child: n.ref.id,
          relation: "FORK",
          comparison: "IDENTICAL",
        })),
        total: descendants ? size : 0,
        limit,
        offset,
        next_offset:
          descendants && offset + limit < size ? offset + limit : null,
        truncated: false,
      } satisfies Schema["LineageGraph"];
    } else if (path.endsWith("/lineage/diff"))
      data = {
        ref: root.ref,
        parent: null,
        comparison: root.comparison,
        files: [],
        total: 0,
        limit: 50,
        offset: 0,
        next_offset: null,
      };
    else if (path.endsWith("/trust/compare")) {
      const page = changes.slice(offset, offset + 50);
      requests.push({ path, offset, returned: page.length });
      data = {
        before_version: uuid(0),
        after_version: uuid(1),
        before_scan: scanId,
        after_scan: scanId,
        comparable: true,
        tool_conditions_equal: true,
        manifest_equal: false,
        reasons: [],
        changes: page,
        total: size,
        next_offset: offset + 50 < size ? offset + 50 : null,
        counts: {
          "resource:modified": Math.floor(size / 2),
          "resource:added": Math.ceil(size / 2),
        },
        interpretation:
          "정적 비교 · added/modified는 색상 외 텍스트로 표시합니다.",
      } satisfies Schema["VersionComparison"];
    } else if (path.endsWith("/trust/reuse"))
      data = { records: [], next_offset: null, metric_note: "측정 기록 없음" };
    else if (path.endsWith("/trust/timeline"))
      data = {
        version_id: uuid(0),
        scan_id: scanId,
        events: [],
        unrecorded: [],
        signature_status: "unsigned",
        signature_reasons: ["NO_SIGNATURE"],
        observed_at: date,
        security_guarantee: false,
      };
    else if (path.endsWith("/risk-paths")) {
      const page = Array.from({ length: Math.min(20, size - offset) }, (_, i) =>
        riskPath(offset + i),
      );
      requests.push({ path, offset, returned: page.length });
      data = {
        scan_id: scanId,
        incomplete: false,
        paths: page,
        next_offset: offset + 20 < size ? offset + 20 : null,
      } satisfies Schema["RiskPaths"];
    } else if (path.startsWith("/api/dynamic-demo/")) data = run;
    else throw new Error(`Unexpected API request: ${path}`);
    await route.fulfill({
      json: data,
      headers: {
        "access-control-allow-origin": request.headers().origin ?? "*",
        "access-control-allow-credentials": "true",
      },
    });
  });
  return requests;
}
