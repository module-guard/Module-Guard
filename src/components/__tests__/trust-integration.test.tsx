import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
const result = vi.hoisted(() => ({
  data: undefined as unknown,
  error: "",
  retry: () => {},
}));
vi.mock("@/api/hooks", () => ({ useApi: () => result }));
vi.mock("../graph/graph-canvas", () => ({ GraphCanvas: () => null }));
import { DynamicDemo } from "../dynamic-demo";
import { ReputationEvidence } from "../reputation-evidence";
import { RiskPaths } from "../risk-paths";
import { ReuseEvidence } from "../reuse-evidence";
import { VerificationTimeline } from "../trust-explorer";
const props = { owner: "alice", name: "demo", version: "v1" };
describe("trust uncertainty and evidence", () => {
  it.each([
    "success",
    "failed",
    "aborted",
    "timeout",
    "not_run",
    "unobservable",
  ])("labels %s fixture without execution claims", (scenario) => {
    result.data = JSON.parse(
      readFileSync(`backend/app/dynamic/fixtures/${scenario}.json`, "utf8"),
    );
    const html = renderToStaticMarkup(<DynamicDemo />);
    expect(html).toContain("실제 worker 미연결");
    expect(html).toContain("실제 실행: 없음");
    expect(html).toContain("이 모듈·검사의 실행 결과가 아닙니다");
  });
  it("does not turn empty reputation into zero or a security score", () => {
    result.data = { entries: [] };
    const html = renderToStaticMarkup(
      <ReputationEvidence {...props} canCollect={false} />,
    );
    expect(html).toContain("수집된 근거가 없습니다");
    expect(html).toContain("총점을 제공하지 않습니다");
    expect(html).not.toContain("0%");
  });
  it("separates unknown risk paths from safety", () => {
    result.data = { incomplete: true, paths: [] };
    const html = renderToStaticMarkup(<RiskPaths id="scan" />);
    expect(html).toContain("경로 누락 가능성");
    expect(html).toContain("안전성을 보증하지 않습니다");
  });
  it("does not invent savings or events", () => {
    result.data = { records: [], metric_note: "측정 기록 없음" };
    expect(renderToStaticMarkup(<ReuseEvidence {...props} />)).toContain(
      "측정 기록 없음",
    );
    result.data = {
      events: [],
      signature_status: "unsigned",
      unrecorded: ["다운로드 검증"],
    };
    const html = renderToStaticMarkup(<VerificationTimeline {...props} />);
    expect(html).toContain("기록된 이벤트가 없습니다");
    expect(html).toContain("unsigned");
  });
  it("renders failures and loading", () => {
    result.data = undefined;
    result.error = "테스트 연결 실패";
    expect(renderToStaticMarkup(<DynamicDemo />)).toContain("테스트 연결 실패");
    result.error = "";
    expect(renderToStaticMarkup(<DynamicDemo />)).toBeTruthy();
  });
});
