"use client";
import { useState, type Ref } from "react";
import { ChevronDown } from "lucide-react";
import { Badge, type Scan } from "./live-shared";

const reasons: Record<string, string> = {
  FILE_PARSE_FAILED:
    "구문 오류, 지원하지 않는 파일 구조 또는 파일 크기 제한으로 파일을 분석하지 못했습니다.",
  UNSAFE_PATH: "심볼릭 링크 경로는 안전 정책에 따라 읽지 않았습니다.",
  UNSAFE_MODULE_PATH:
    "저장소 밖을 가리키거나 심볼릭 링크를 사용하는 로컬 모듈 경로를 읽지 않았습니다.",
  EXCLUDED_MODULE_PATH:
    "분석 제외 디렉터리에 있는 로컬 모듈을 읽지 않았습니다.",
  MISSING_LOCAL_MODULE: "로컬 모듈 디렉터리를 찾지 못했습니다.",
  MODULE_CYCLE: "로컬 모듈 호출이 순환하여 추가 탐색을 중단했습니다.",
  NO_HCL_FILES: "이 모듈 디렉터리에 분석할 Terraform 파일이 없습니다.",
  ENTITY_LIMIT:
    "구성 요소 수 상한에 도달하여 나머지 선언을 분석하지 못했습니다.",
  ADDRESS_LIMIT: "선언 주소 길이가 저장 상한을 넘어 해당 선언을 제외했습니다.",
  DEPTH_LIMIT:
    "로컬 모듈 탐색 깊이 상한에 도달하여 더 깊은 모듈을 읽지 않았습니다.",
  DUPLICATE_ADDRESS: "같은 주소의 선언이 중복되어 추가 선언을 제외했습니다.",
  UNSUPPORTED_JSON:
    "이 검사는 Terraform JSON 파일의 구조 분석을 지원하지 않았습니다.",
  UNSUPPORTED_JSON_BLOCK:
    "지원하지 않는 Terraform JSON 블록을 구조 분석에서 제외했습니다.",
  UNSUPPORTED_JSON_DYNAMIC:
    "Terraform JSON dynamic 블록의 실제 인스턴스는 확장하지 않았습니다.",
  DYNAMIC_ITERATOR_FLOW_UNRESOLVED:
    "동적 반복자의 실제 인스턴스별 데이터 흐름은 분석하지 않았습니다.",
  COLLECTION_FLOW_AGGREGATED:
    "반복문은 컬렉션 단위로 분석하며 개별 항목의 값은 평가하지 않았습니다.",
  FLOW_EDGE_LIMIT:
    "데이터 흐름 연결 수 상한에 도달하여 일부 흐름만 보존했습니다.",
  EXPRESSION_ANALYSIS_FAILED: "일부 표현식의 정적 참조를 분석하지 못했습니다.",
};
const stageLabels: Record<string, string> = {
  checkov: "Checkov 보안 검사",
  parse: "Terraform 코드 분석",
  graph: "의존성 그래프",
  source_trust: "공급망 신뢰",
  data_flow: "데이터 흐름",
  correlation: "상관 위험",
  persistence: "결과 저장",
};

// Some older graphs store limit diagnostics under this shared code. Match only
// these fixed messages; never render diagnostic messages or expression text.
function reason(diagnostic: Record<string, unknown>, code: string) {
  if (code === "EXPRESSION_ANALYSIS_FAILED") {
    if (diagnostic.message === "Graph edge limit reached.")
      return "의존성 연결 수 상한에 도달하여 일부 연결만 보존했습니다.";
    if (diagnostic.message === "Reference limit reached.")
      return "정적 참조 수 상한에 도달하여 일부 참조만 보존했습니다.";
  }
  return (
    reasons[code] ??
    "이 항목은 상세 원인 설명을 지원하지 않습니다. 진단 코드와 제공된 경로를 확인해 주세요."
  );
}
export function analysisIncomplete(scan: Scan) {
  return Boolean(
    scan.partial_result ||
    scan.status === "FAILED" ||
    (scan.analysis?.files_failed ?? 0) > 0 ||
    scan.analysis?.diagnostics?.length ||
    Object.values(scan.analysis?.stages ?? {}).some(
      (stage) =>
        ["PARTIAL", "FAILED", "SKIPPED"].includes(stage.status) ||
        (scan.status === "COMPLETED" && stage.status !== "COMPLETED"),
    ),
  );
}
export function coverageNotice(scan: Scan) {
  if (scan.status === "FAILED")
    return "검사가 실패했습니다. 저장된 부분 결과가 있더라도 전체 분석이 성공한 것은 아닙니다.";
  if (!["COMPLETED", "FAILED"].includes(scan.status))
    return "분석이 진행 중입니다. 파일 수와 진단은 중간 집계입니다.";
  if (analysisIncomplete(scan))
    return "일부 분석이 완료되지 않았습니다. 탐지 0건이어도 미분석 파일·모듈의 안전을 뜻하지 않습니다.";
  if (!scan.analysis)
    return "이 검사에는 분석 범위 상세 정보가 없습니다. 누락 여부를 확인할 수 없습니다.";
  return Object.keys(scan.analysis.stages ?? {}).length
    ? "기록된 분석 단계가 모두 완료되었습니다. 아래에서 분석 범위의 제한을 확인할 수 있습니다."
    : "기록된 진단이 없습니다. 분석 범위 밖의 코드까지 검사했다는 의미는 아닙니다.";
}

export function AnalysisCoverage({
  scan,
  open = false,
  onOpenChange,
  summaryRef,
}: {
  scan: Scan;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  summaryRef?: Ref<HTMLElement>;
}) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const analysis = scan.analysis;
  const diagnostics = (analysis?.diagnostics ?? []).map((diagnostic) => {
    const code =
      typeof diagnostic.code === "string" &&
      /^[A-Z][A-Z0-9_]{0,79}$/.test(diagnostic.code)
        ? diagnostic.code
        : "UNKNOWN_DIAGNOSTIC";
    const modulePath = diagnostic.module_path;
    return {
      code,
      file:
        typeof diagnostic.file_path === "string" && diagnostic.file_path
          ? diagnostic.file_path
          : "제공되지 않음",
      module:
        Array.isArray(modulePath) &&
        modulePath.every((part) => typeof part === "string")
          ? modulePath.length
            ? modulePath.join(" / ")
            : "root"
          : "제공되지 않음",
      reason: reason(diagnostic, code),
    };
  });
  const search = query.trim().toLocaleLowerCase();
  const filtered = diagnostics.filter((item) =>
    [item.code, item.file, item.module, item.reason].some((value) =>
      value.toLocaleLowerCase().includes(search),
    ),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / 20));
  const current = Math.min(page, pages - 1);
  const unfinished = Object.entries(analysis?.stages ?? {}).filter(
    ([, stage]) => stage.status !== "COMPLETED",
  );
  return (
    <section
      aria-label="분석 범위 안내"
      className="my-5 min-w-0 rounded-lg border border-line p-4 sm:p-5"
    >
      <p
        className={
          analysisIncomplete(scan) ? "text-amber-800" : "text-slate-600"
        }
      >
        {coverageNotice(scan)}
      </p>
      <details
        open={open}
        onToggle={(event) => onOpenChange?.(event.currentTarget.open)}
        className="group mt-2"
      >
        <summary
          ref={summaryRef}
          className="flex min-h-11 cursor-pointer list-none items-center gap-2 font-semibold text-brand focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brand"
        >
          분석 범위·누락 원인
          <ChevronDown
            size={16}
            aria-hidden="true"
            className="transition group-open:rotate-180"
          />
        </summary>
        {!analysis ? (
          <p className="mt-3 text-slate-500">
            파일 집계와 상세 진단이 저장되지 않은 검사입니다.
          </p>
        ) : (
          <>
            <dl className="my-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
              {(
                [
                  ["분석 성공 파일", analysis.files_parsed],
                  ["분석 실패 파일", analysis.files_failed],
                  ["미해결 참조", analysis.unresolved_references],
                  ["외부 모듈", analysis.external_modules],
                ] as const
              ).map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs text-slate-500">{label}</dt>
                  <dd className="mt-1 font-semibold tabular-nums">
                    {typeof value === "number" ? value : "정보 없음"}
                  </dd>
                </div>
              ))}
            </dl>
            <p className="text-xs text-slate-500">
              파일 수는 구조 분석 파서가 처리한 Terraform 파일 기준이며 Checkov
              검사 범위와 다를 수 있습니다.
            </p>
            {unfinished.length > 0 && (
              <ul
                aria-label="완료되지 않은 분석 단계"
                className="mt-3 flex flex-wrap gap-3 text-xs"
              >
                {unfinished.map(([key, stage]) => (
                  <li key={key}>
                    {stageLabels[key] ?? "분석 단계"}{" "}
                    <Badge value={stage.status} />
                  </li>
                ))}
              </ul>
            )}
            {((analysis.external_modules ?? 0) > 0 ||
              analysis.external_transitive_depth_unknown) && (
              <p className="mt-3 text-sm text-slate-600">
                외부 모듈은 선언된 연결을 표시하지만 내부 코드와 전이 의존 관계
                전체를 분석한 결과는 아닙니다.
              </p>
            )}
            {(analysis.unresolved_references ?? 0) > 0 && (
              <p className="mt-3 text-sm text-slate-600">
                미해결 참조는 의존 대상을 확인하지 못한 연결입니다. 탐지 건수나
                실패 파일 수와는 다릅니다.
              </p>
            )}
            {diagnostics.length > 0 ? (
              <>
                <label className="mt-5 block text-sm">
                  진단 검색
                  <input
                    type="search"
                    value={query}
                    className="input mt-2"
                    style={{ fontSize: "16px" }}
                    placeholder="진단 코드, 파일·모듈 경로, 원인"
                    onChange={(event) => {
                      setQuery(event.target.value);
                      setPage(0);
                    }}
                  />
                </label>
                <p role="status" className="my-3 text-xs text-slate-500">
                  전체 {diagnostics.length}건 중 {filtered.length}건 일치 ·{" "}
                  {filtered.length ? current * 20 + 1 : 0}–
                  {Math.min((current + 1) * 20, filtered.length)}건 표시
                </p>
                <ul aria-label="분석 진단" className="space-y-3">
                  {filtered
                    .slice(current * 20, (current + 1) * 20)
                    .map((item, index) => (
                      <li
                        key={current * 20 + index}
                        className="min-w-0 rounded border border-line p-3"
                      >
                        <p className="break-all font-mono text-xs font-semibold">
                          {item.code}
                        </p>
                        <p className="mt-2 text-sm">{item.reason}</p>
                        <dl className="mt-2 space-y-1 text-xs">
                          <div>
                            <dt className="text-slate-500">파일·경로</dt>
                            <dd className="break-all font-mono">{item.file}</dd>
                          </div>
                          <div>
                            <dt className="text-slate-500">모듈 경로</dt>
                            <dd className="break-all font-mono">
                              {item.module}
                            </dd>
                          </div>
                        </dl>
                      </li>
                    ))}
                </ul>
                {!filtered.length && (
                  <p className="text-sm text-slate-500">
                    일치하는 진단이 없습니다.
                  </p>
                )}
                {pages > 1 && (
                  <nav
                    aria-label="분석 진단 페이지"
                    className="mt-3 flex flex-wrap items-center gap-3 text-sm"
                  >
                    <button
                      type="button"
                      className="min-h-11 px-3 text-brand"
                      disabled={current === 0}
                      onClick={() => setPage(current - 1)}
                    >
                      이전 진단
                    </button>
                    <span>
                      {current + 1} / {pages} 페이지
                    </span>
                    <button
                      type="button"
                      className="min-h-11 px-3 text-brand"
                      disabled={current === pages - 1}
                      onClick={() => setPage(current + 1)}
                    >
                      다음 진단
                    </button>
                  </nav>
                )}
              </>
            ) : (
              <p className="mt-4 text-sm text-slate-500">
                기록된 진단이 없습니다. 진단이 비어 있어도 분석 범위 밖의
                코드까지 검사했다는 의미는 아닙니다.
              </p>
            )}
          </>
        )}
      </details>
    </section>
  );
}
