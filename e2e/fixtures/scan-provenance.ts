import { scanFixture } from "./analysis-coverage";

export const provenanceScan = scanFixture({
  provenance: {
    scanner_name: "checkov",
    checkov_version: "3.3.26",
    moduleguard_version: "0.1.0",
    build_commit: "a".repeat(40),
    rules_version: "b".repeat(64),
    severity_rules_version: "c".repeat(64),
    remediation_rules_version: "d".repeat(64),
    analysis_options: { max_analysis_entities: 20000, token: "SECRET" },
    checkov_options: {
      framework: "terraform",
      skip_path: "(^|/)(vendor)(/|$)",
      skip_download: true,
      evaluate_variables: false,
      internal_path: "/SECRET",
    },
    stderr: "SECRET",
  },
  postprocessing: {
    applied_at: "2026-11-03T01:00:00Z",
    rules_version: "e".repeat(64),
    severity_rules_version: "f".repeat(64),
    remediation_rules_version: "a".repeat(64),
    history: [
      {
        applied_at: "2026-11-02T01:00:00Z",
        rules_version: "1".repeat(64),
        stderr: "SECRET",
      },
    ],
  },
});
