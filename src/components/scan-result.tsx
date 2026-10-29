"use client";
import { useState } from "react";
import Link from "next/link";
import { Check, ChevronDown, Copy } from "lucide-react";
import { explainScanError } from "@/api/errors";
import { api, unwrap } from "@/api/client";
import { useApi } from "@/api/hooks";
import { IntegritySummary } from "./module-integrity";
import {
  ExampleNote,
  Panel,
  State,
  Badge,
  DemoLabel,
  SeverityBars,
  Time,
  type Scan,
} from "./live-shared";
import { FindingsTab, DependenciesTab, FlowsTab } from "./analysis-tabs";
const tabs = [
  { name: "요약" },
  { name: "보안 설정", scanner: "checkov" },
  { name: "공급망", scanner: "source_trust" },
  { name: "의존성" },
  { name: "데이터 흐름" },
  { name: "상관 위험", scanner: "correlation" },
];
function CopyId({ id }: { id: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      title={id}
      aria-label="검사 ID 복사"
      className="flex items-center gap-1 text-xs text-slate-400 hover:text-navy"
      onClick={async () => {
        await navigator.clipboard.writeText(id);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
      {copied ? "복사됨" : "ID 복사"}
    </button>
  );
}
export function ScanResult({ id }: { id: string }) {
  const [tab, setTab] = useState(0);
  const r = useApi(
    (s) =>
      unwrap(
        api.GET("/api/scans/{scan_id}", {
          signal: s,
          params: { path: { scan_id: id } },
        }),
      ),
    id,
    (s) => !["COMPLETED", "FAILED"].includes(s.status),
  );
  if (!r.data) return <State {...r} />;
  const s = r.data;
  return (
    <div className="page-enter">
      <h1>{s.module.name}</h1>
      {s.repository && (
        <p className="mt-2 text-slate-600">
          <Link
            className="font-semibold text-brand"
            href={`/@${s.repository.owner}/${s.repository.name}`}
          >
            @{s.repository.owner}/{s.repository.name}
          </Link>{" "}
          {s.repository.version} ·{" "}
          {s.repository.visibility === "PRIVATE" ? "비공개" : "공개"}
          {s.status === "COMPLETED"
            ? "로 등록됨"
            : s.status === "FAILED"
              ? " · 검사에 실패해 등록되지 않았습니다"
              : " · 검사가 끝나면 등록됩니다"}
        </p>
      )}
      <DemoLabel demo={s.module.is_demo} />
      <div className="my-4 flex flex-wrap items-center gap-2">
        <Badge value={s.status} />
        {s.partial_result && <Badge value="PARTIAL" />}
        <CopyId id={s.id} />
      </div>
      {r.error && <State error={r.error} retry={r.retry} />}
      <Progress scan={s} />
      <IntegritySummary info={s.integrity} />
      {s.error_message && (
        <Panel>
          <p role="alert" className="text-red-700">
            {explainScanError(s.error_message)}
          </p>
          <p className="mt-2">
            완료된 분석 단계의 결과는 아래에서 확인할 수 있습니다.
          </p>
        </Panel>
      )}
      <nav
        className="my-5 flex gap-5 overflow-x-auto border-b border-line"
        aria-label="검사 결과 탭"
      >
        {tabs.map((t, i) => {
          const count = t.scanner
            ? // 탭의 숫자는 모듈 본체 기준이다(예제·테스트 제외).
              (s.scope_summary?.module_by_scanner ??
                s.analysis?.scanner_breakdown)?.[t.scanner]
            : undefined;
          return (
            <button
              key={t.name}
              className={`whitespace-nowrap border-b-2 px-1 py-3 ${tab === i ? "border-brand font-semibold text-brand" : "border-transparent text-slate-500"}`}
              onClick={() => setTab(i)}
              aria-pressed={tab === i}
            >
              {t.name}
              {count != null && (
                <span className="ml-1.5 text-xs font-normal tabular-nums text-slate-400">
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </nav>
      {tab === 0 ? (
        <Summary scan={s} />
      ) : !["COMPLETED", "FAILED"].includes(s.status) ? (
        <Panel>분석 중입니다. 결과 저장이 완료되면 조회할 수 있습니다.</Panel>
      ) : tab === 1 ? (
        <FindingsTab
          key="checkov"
          id={id}
          scanner="checkov"
          examples={s.scope_summary?.example_by_scanner?.checkov ?? 0}
        />
      ) : tab === 2 ? (
        <FindingsTab
          key="source_trust"
          id={id}
          scanner="source_trust"
          examples={s.scope_summary?.example_by_scanner?.source_trust ?? 0}
        />
      ) : tab === 3 ? (
        <DependenciesTab id={id} />
      ) : tab === 4 ? (
        <FlowsTab
          id={id}
          partial={s.analysis?.stages?.data_flow?.status === "PARTIAL"}
        />
      ) : (
        <FindingsTab key="correlation" id={id} scanner="correlation" />
      )}
    </div>
  );
}
const stageNames = [
  ["collect", "소스 수집"],
  ["checkov", "Checkov 보안 검사"],
  ["parse", "Terraform 코드 분석"],
  ["graph", "의존성 그래프"],
  ["source_trust", "공급망 신뢰"],
  ["data_flow", "데이터 흐름"],
  ["correlation", "상관 위험"],
  ["persistence", "결과 저장"],
];
function stageStatus(s: Scan, key: string) {
  if (s.failed_stage === key) return "FAILED";
  if (key !== "collect") return s.analysis?.stages?.[key]?.status || "PENDING";
  // 업로드한 모듈은 commit이 없다. 분석이 시작됐으면 수집은 끝난 것이다.
  if (s.commit_hash || s.analysis) return "COMPLETED";
  if (s.status === "COLLECTING") return "RUNNING";
  return s.status === "FAILED" ? "FAILED" : "PENDING";
}
function Progress({ scan: s }: { scan: Scan }) {
  const stages = stageNames.map(([key, label]) => ({
    key,
    label,
    status: stageStatus(s, key),
  }));
  const grid = (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {stages.map(({ key, label, status }) => (
        <div key={key} className="rounded-lg border border-line bg-white p-3">
          <p className="mb-2 text-xs">{label}</p>
          <Badge value={status} />
        </div>
      ))}
    </div>
  );
  // 진행 중일 때만 크게 보여 주고, 끝난 뒤에는 한 줄로 접는다.
  if (!["COMPLETED", "FAILED"].includes(s.status))
    return <Panel title="분석 진행">{grid}</Panel>;
  const count = (status: string) =>
    stages.filter((x) => x.status === status).length;
  const line = [
    `${stages.length}단계 중 ${count("COMPLETED")}단계 완료`,
    count("PARTIAL") && `${count("PARTIAL")}단계 부분 분석`,
    count("FAILED") && `${count("FAILED")}단계 실패`,
    count("SKIPPED") && `${count("SKIPPED")}단계 건너뜀`,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <details className="group my-4">
      <summary className="flex cursor-pointer list-none items-center gap-1 text-slate-500 hover:text-navy">
        {line}
        <ChevronDown size={15} className="transition group-open:rotate-180" />
      </summary>
      <div className="mt-3">{grid}</div>
    </details>
  );
}
const stageWords: Record<string, string> = {
  parse: "Terraform 코드",
  graph: "의존성 그래프",
  source_trust: "공급망 신뢰",
  data_flow: "데이터 흐름",
  correlation: "상관 위험",
};
function explainWarning(w: string) {
  const stage = w.match(/^(\w+) analysis is partial/)?.[1];
  return stage && stageWords[stage]
    ? `${stageWords[stage]} 분석이 일부만 완료되었습니다.`
    : w;
}
function Summary({ scan: s }: { scan: Scan }) {
  const internals = Object.entries({
    "Terraform 구성 요소": s.analysis?.entities,
    "의존성 연결": s.analysis?.dependency_edges,
    "데이터 흐름": s.analysis?.data_flow?.flows,
    "검사 완료 시각":
      s.finished_at && new Date(s.finished_at).toLocaleString("ko-KR"),
  }).filter(([, v]) => v != null);
  return (
    <>
      <section className="max-w-2xl">
        <h2 className="mb-4">
          위험도 분포{" "}
          <span className="ml-1 font-normal tabular-nums text-slate-400">
            탐지 {s.summary.total}
          </span>
        </h2>
        <SeverityBars summary={s.summary} />
        <ExampleNote summary={s.scope_summary} />
      </section>
      <dl className="mt-8 flex flex-wrap gap-x-12 gap-y-4">
        {s.duration_ms != null && (
          <div>
            <dt className="text-xs text-slate-400">소요 시간</dt>
            <dd className="mt-1 tabular-nums">
              {(s.duration_ms / 1000).toFixed(1)}초
            </dd>
          </div>
        )}
        {s.commit_hash && (
          <div>
            <dt className="text-xs text-slate-400">Commit</dt>
            <dd className="mt-1 font-mono" title={s.commit_hash}>
              {s.commit_hash.slice(0, 12)}
            </dd>
          </div>
        )}
        <div>
          <dt className="text-xs text-slate-400">검사 시각</dt>
          <dd className="mt-1">
            <Time value={s.started_at || s.created_at} />
          </dd>
        </div>
      </dl>
      {s.warnings.length > 0 && (
        <ul className="mt-6 space-y-1 text-xs text-amber-800">
          {s.warnings.map((w, i) => (
            <li key={i}>{explainWarning(w)}</li>
          ))}
        </ul>
      )}
      {internals.length > 0 && (
        <details className="group mt-6">
          <summary className="flex cursor-pointer list-none items-center gap-1 text-slate-500 hover:text-navy">
            상세 정보
            <ChevronDown
              size={15}
              className="transition group-open:rotate-180"
            />
          </summary>
          <dl className="mt-3 flex flex-wrap gap-x-12 gap-y-4">
            {internals.map(([k, v]) => (
              <div key={k}>
                <dt className="text-xs text-slate-400">{k}</dt>
                <dd className="mt-1 tabular-nums">{v}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </>
  );
}
