import type { Scan } from "../../src/components/live-shared";

type Analysis = NonNullable<Scan["analysis"]>;
const date = "2026-10-29T00:00:00Z";
export const completeAnalysis: Analysis = {
  schema_version: 1,
  stages: {
    checkov: { status: "COMPLETED" },
    parse: { status: "COMPLETED" },
    graph: { status: "COMPLETED" },
    source_trust: { status: "COMPLETED" },
    data_flow: { status: "COMPLETED" },
    correlation: { status: "COMPLETED" },
    persistence: { status: "COMPLETED" },
  },
  entities: 12,
  edges: 8,
  dependency_edges: 8,
  modules: 1,
  external_modules: 0,
  providers: 1,
  source_trust_findings: 0,
  unresolved_references: 0,
  files_parsed: 12,
  files_failed: 0,
  max_observed_module_depth: 1,
  external_transitive_depth_unknown: false,
  diagnostics: [],
};

// API-shaped fixtures: errors are deliberately hostile to catch accidental raw output.
export const partialAnalysis: Analysis = {
  ...completeAnalysis,
  stages: {
    ...completeAnalysis.stages,
    parse: { status: "PARTIAL", message: "SECRET_STAGE_STDERR" },
  },
  files_failed: 2,
  external_modules: 1,
  external_transitive_depth_unknown: true,
  unresolved_references: 7,
  diagnostics: [
    {
      code: "FILE_PARSE_FAILED",
      file_path: "broken.tf",
      module_path: [],
      message: "SECRET_RAW_STDERR",
    },
    {
      code: "UNSUPPORTED_JSON_DYNAMIC",
      file_path: "modules/network/security.tf.json",
      module_path: ["network"],
      message: "SECRET_DYNAMIC_EXPRESSION",
    },
    {
      code: "ENTITY_LIMIT",
      file_path: "modules/network",
      module_path: ["network"],
      message: "SECRET_ENTITY_CONTENT",
    },
  ],
};

export function scanFixture(overrides: Partial<Scan> = {}): Scan {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    module_id: "22222222-2222-4222-8222-222222222222",
    module: {
      id: "22222222-2222-4222-8222-222222222222",
      name: "coverage-fixture",
      source_type: "UPLOAD",
      source_url: "upload://coverage-fixture",
      is_demo: false,
      created_at: date,
      updated_at: date,
      default_branch: null,
      latest_version: null,
      provider: null,
      repository_name: null,
      repository_owner: null,
    },
    status: "COMPLETED",
    scanner: "checkov",
    branch: null,
    commit_hash: null,
    created_at: date,
    started_at: date,
    finished_at: date,
    duration_ms: 1200,
    error_message: null,
    partial_result: false,
    warnings: [],
    summary: {
      total: 0,
      critical: 0,
      high: 0,
      medium: 0,
      low: 0,
      info: 0,
      unknown: 0,
    },
    analysis: completeAnalysis,
    ...overrides,
  };
}
