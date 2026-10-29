import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import {
  completeAnalysis,
  partialAnalysis,
  scanFixture,
} from "../../../e2e/fixtures/analysis-coverage";
import {
  AnalysisCoverage,
  analysisIncomplete,
  coverageNotice,
} from "../analysis-coverage";

describe("analysis coverage", () => {
  it("shows the failed file, module, count and safe reasons for partial results", () => {
    const scan = scanFixture({ analysis: partialAnalysis });
    const html = renderToStaticMarkup(<AnalysisCoverage scan={scan} open />);
    expect(html).toContain("일부 분석이 완료되지 않았습니다.");
    expect(html).toContain("탐지 0건이어도");
    expect(html).toContain("broken.tf");
    expect(html).toContain("modules/network/security.tf.json");
    expect(html).toContain("UNSUPPORTED_JSON_DYNAMIC");
    expect(html).toContain("실제 인스턴스는 확장하지 않았습니다.");
    expect(html).toContain("구성 요소 수 상한에 도달");
    expect(html).toContain(">network<");
    expect(html).toContain(">root<");
    expect(html).toMatch(/분석 성공 파일<\/dt><dd[^>]*>12<\/dd>/);
    expect(html).toMatch(/분석 실패 파일<\/dt><dd[^>]*>2<\/dd>/);
    expect(html).not.toContain("SECRET_");
  });

  it.each(["PARTIAL", "FAILED", "SKIPPED", "PENDING"] as const)(
    "warns on a %s stage even when the overall scan is completed and partial_result is false",
    (status) => {
      const scan = scanFixture({
        analysis: { ...completeAnalysis, stages: { parse: { status } } },
      });
      expect(analysisIncomplete(scan)).toBe(true);
      expect(coverageNotice(scan)).toContain("일부 분석");
    },
  );

  it("distinguishes preserved failed-scan results from a completely successful scan", () => {
    const failed = scanFixture({ status: "FAILED", analysis: partialAnalysis });
    expect(coverageNotice(failed)).toContain("검사가 실패했습니다.");
    expect(coverageNotice(failed)).toContain("저장된 부분 결과");
    expect(
      renderToStaticMarkup(<AnalysisCoverage scan={failed} open />),
    ).toContain("broken.tf");
    const complete = scanFixture();
    expect(analysisIncomplete(complete)).toBe(false);
    expect(coverageNotice(complete)).toContain("분석 단계가 모두 완료");
    expect(coverageNotice(complete)).not.toContain("일부 분석이 완료되지");
  });

  it("explains external boundaries and unresolved references separately from file failures", () => {
    const html = renderToStaticMarkup(
      <AnalysisCoverage
        scan={scanFixture({ analysis: partialAnalysis })}
        open
      />,
    );
    expect(html).toContain("외부 모듈은 선언된 연결");
    expect(html).toContain("전이 의존 관계 전체를 분석한 결과는 아닙니다");
    expect(html).toContain("미해결 참조는 의존 대상을 확인하지 못한 연결");
    expect(html).toContain("실패 파일 수와는 다릅니다");
  });

  it("handles analysis-null and older empty diagnostics without claiming full coverage", () => {
    const legacy = scanFixture({ analysis: null });
    const html = renderToStaticMarkup(<AnalysisCoverage scan={legacy} open />);
    expect(html).toContain("분석 범위 상세 정보가 없습니다");
    expect(html).not.toContain("분석 단계가 모두 완료");
    for (const diagnostics of [[], undefined]) {
      const scan = scanFixture({
        analysis: { ...completeAnalysis, stages: {}, diagnostics },
      });
      expect(
        renderToStaticMarkup(<AnalysisCoverage scan={scan} open />),
      ).toContain("기록된 진단이 없습니다");
    }
  });

  it("does not present running scans as a completed result", () => {
    expect(
      coverageNotice(
        scanFixture({ status: "SCANNING", analysis: partialAnalysis }),
      ),
    ).toContain("중간 집계");
  });

  it("does not expose unknown messages, expressions, stderr or extra metadata", () => {
    const html = renderToStaticMarkup(
      <AnalysisCoverage
        scan={scanFixture({
          analysis: {
            ...completeAnalysis,
            stages: {
              graph: {
                status: "FAILED",
                message: "SECRET_STAGE",
                error_code: "SECRET_ERROR",
              },
            },
            diagnostics: [
              {
                code: "UNKNOWN_CODE",
                file_path: "main.tf",
                message: "SECRET_MESSAGE",
                expression: "SECRET_EXPRESSION",
                stderr: "SECRET_STDERR",
              },
            ],
          },
        })}
        open
      />,
    );
    expect(html).toContain("UNKNOWN_CODE");
    expect(html).toContain("상세 원인 설명을 지원하지 않습니다");
    expect(html).toContain("제공되지 않음");
    expect(html).not.toContain("SECRET_");
  });

  it.each([
    ["Graph edge limit reached.", "의존성 연결 수 상한"],
    ["Reference limit reached.", "정적 참조 수 상한"],
    ["SECRET_RAW_EXPRESSION", "일부 표현식의 정적 참조"],
  ])(
    "distinguishes legacy graph limit messages without rendering them: %s",
    (message, expected) => {
      const scan = scanFixture({
        analysis: {
          ...completeAnalysis,
          diagnostics: [{ code: "EXPRESSION_ANALYSIS_FAILED", message }],
        },
      });
      const html = renderToStaticMarkup(<AnalysisCoverage scan={scan} open />);
      expect(html).toContain(expected);
      expect(html).not.toContain(message);
      expect(html).toContain("제공되지 않음");
    },
  );

  it("escapes paths and rejects unsupported diagnostic field shapes", () => {
    const scan = scanFixture({
      analysis: {
        ...completeAnalysis,
        diagnostics: [
          {
            code: { secret: "SECRET_CODE" },
            file_path: { value: "SECRET_PATH" },
            module_path: [1, "SECRET_MODULE"],
          },
          {
            code: "FILE_PARSE_FAILED",
            file_path: '<img src=x onerror="alert(1)">',
            module_path: [],
          },
        ],
      },
    });
    const html = renderToStaticMarkup(<AnalysisCoverage scan={scan} open />);
    expect(html).not.toContain("SECRET_");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
    expect(html).toContain("UNKNOWN_DIAGNOSTIC");
  });

  it("bounds the initially rendered diagnostic list", () => {
    const scan = scanFixture({
      analysis: {
        ...completeAnalysis,
        diagnostics: Array.from({ length: 45 }, (_, i) => ({
          code: "FILE_PARSE_FAILED",
          file_path: `file-${i}.tf`,
        })),
      },
    });
    const html = renderToStaticMarkup(<AnalysisCoverage scan={scan} open />);
    expect(html).toContain("전체 45건");
    expect(html).toContain("file-19.tf");
    expect(html).not.toContain("file-20.tf");
    expect(html).toContain("다음 진단");
  });
});
