import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { provenanceScan } from "../../../e2e/fixtures/scan-provenance";
import { publicProvenance, ScanProvenance } from "../scan-provenance";

describe("scan provenance", () => {
  it("separates original tools and rules from past and latest reapply", () => {
    const html = renderToStaticMarkup(<ScanProvenance scan={provenanceScan} />);
    for (const value of [
      "3.3.26",
      "최초 검사",
      "최근 후처리",
      "이전 후처리 1",
      "2026-11-02",
      "2026-11-03",
      "소스·ZIP 해시 무결성 검증과 별개",
      "상관 위험을 재분석하지 않습니다",
    ])
      expect(html).toContain(value);
    expect(html).not.toContain("SECRET");
    expect(html).toContain("false");
  });
  it.each([{}, { provenance: null, postprocessing: null }])(
    "shows absent historical records without inventing current versions",
    (scan) => {
      const html = renderToStaticMarkup(<ScanProvenance scan={scan} />);
      expect(html).toContain("이전 검사이거나");
      expect(html).toContain("저장된 후처리 이력이 없습니다");
      expect(html).not.toContain("0.1.0");
    },
  );
  it("distinguishes a missing collected version from absent metadata", () => {
    const html = renderToStaticMarkup(
      <ScanProvenance
        scan={{
          provenance: { ...provenanceScan.provenance, checkov_version: null },
        }}
      />,
    );
    expect(html).toContain("수집 실패 또는 미수집");
    expect(html).toContain("0.1.0");
    expect(html).not.toContain("이전 검사이거나");
  });
  it("copies only public fields including false and zero, without mutating the source", () => {
    const before = JSON.stringify(provenanceScan);
    const data = publicProvenance(provenanceScan);
    expect(JSON.stringify(data)).not.toContain("SECRET");
    expect(data.postprocessing?.history).toHaveLength(1);
    expect(data.provenance?.checkov_options.evaluate_variables).toBe(false);
    expect(JSON.stringify(provenanceScan)).toBe(before);
    expect(
      publicProvenance({
        provenance: {
          analysis_options: { max_flow_depth: 0, max_flow_paths: "SECRET" },
        },
      }).provenance?.analysis_options,
    ).toEqual({ max_flow_depth: 0 });
  });
  it("handles malformed dictionaries and history safely", () => {
    const data = publicProvenance({
      provenance: { checkov_version: {}, analysis_options: [] },
      postprocessing: { history: [null, [], 2, { applied_at: "SECRET" }] },
    });
    expect(data.provenance?.checkov_version).toBeNull();
    expect(data.postprocessing?.history).toHaveLength(1);
    expect(data.postprocessing?.history[0].applied_at).toBeNull();
  });
});
