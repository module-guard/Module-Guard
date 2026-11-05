"use client";
import { useState } from "react";
import { api, unwrap } from "@/api/client";
import { useApi } from "@/api/hooks";
import type { components } from "@/api/generated/schema";
import { GraphCanvas } from "./graph/graph-canvas";
import { State } from "./live-shared";
type Status = components["schemas"]["DynamicRun"]["status"];
const words: Record<Status, string> = {
  success: "성공",
  failed: "실패",
  aborted: "중단",
  timeout: "시간 초과",
  not_run: "미실행",
  unobservable: "관측 불가",
};
export function DynamicDemo() {
  const [scenario, setScenario] = useState<Status>("success"),
    [filter, setFilter] = useState(""),
    [selected, setSelected] = useState("");
  const r = useApi(
    (signal) =>
      unwrap(
        api.GET("/api/dynamic-demo/{scenario}", {
          params: { path: { scenario } },
          signal,
        }),
      ),
    scenario,
  );
  const run = r.data,
    stages = (run?.stages ?? []).filter((s) => !filter || s.status === filter);
  return (
    <section
      aria-label="동적 분석 비실행 데모"
      className="my-5 space-y-4 rounded border border-amber-400 p-4"
    >
      <h2 className="text-xl font-bold">동적 분석 · 비실행 데모</h2>
      <p role="status">
        실제 worker 미연결. 합성 fixture와 가상 시각이며 이 모듈·검사의 실행
        결과가 아닙니다. Terraform 코드를 실행하지 않았습니다.
      </p>
      <label>
        데모 시나리오{" "}
        <select
          value={scenario}
          onChange={(e) => {
            setScenario(e.target.value as Status);
            setSelected("");
          }}
        >
          {Object.entries(words).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </label>
      {!run ? (
        <State {...r} />
      ) : (
        <>
          <p>
            정적 분석 / mock 실행 / 실제 관측은 서로 다릅니다. 현재 공급 계층:{" "}
            {run.mode} · 실제 실행: {run.execution_occurred ? "있음" : "없음"}
          </p>
          <p>
            실행 ID: {run.run_id} · 결과: {words[run.status]}
          </p>
          <details>
            <summary>입력·환경·정책 (재실행 기능 없음)</summary>
            <dl className="break-all">
              <dt>입력 digest</dt>
              <dd>{run.input_digest}</dd>
              <dt>manifest digest</dt>
              <dd>{run.manifest_digest}</dd>
              <dt>이미지 digest</dt>
              <dd>{run.environment.image_digest}</dd>
              <dt>정책</dt>
              <dd>{run.environment.policy_version}</dd>
              <dt>도구</dt>
              <dd>{run.environment.terraform_version}</dd>
              <dt>mock</dt>
              <dd>{run.environment.mock_version ?? "없음"}</dd>
              <dt>lockfile</dt>
              <dd>{run.environment.lockfile_digest ?? "기록 없음"}</dd>
            </dl>
          </details>
          <label>
            단계 상태 필터{" "}
            <select value={filter} onChange={(e) => setFilter(e.target.value)}>
              <option value="">전체</option>
              {Object.entries(words).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <ol>
            {stages.slice(0, 100).map((s) => (
              <li className="border-l-2 p-3" key={s.id}>
                <button
                  onClick={() => setSelected(s.evidence_ids?.[0] ?? "")}
                  className="text-brand underline"
                >
                  {s.name} · {words[s.status]}
                </button>
                <p>
                  {s.started_at ?? "시작 기록 없음"} →{" "}
                  {s.finished_at ?? "완료 기록 없음"}
                </p>
                <p>
                  가상 소요 시간:{" "}
                  {s.started_at && s.finished_at
                    ? `${Date.parse(s.finished_at) - Date.parse(s.started_at)}ms`
                    : "측정 기록 없음"}
                </p>
              </li>
            ))}
          </ol>
          {stages.length > 100 && (
            <p>첫 100개 단계 표시 · 필터로 좁혀 주세요.</p>
          )}
          <GraphCanvas
            label="합성 관측 그래프 (실제 실행 아님)"
            nodes={run.nodes?.map((n) => ({
              id: n.id,
              address: n.id,
              type: n.resource_type,
            }))}
            edges={run.edges?.map((e) => ({ ...e, type: "합성 연결" }))}
            onCenter={(address) =>
              setSelected(
                run.nodes?.find((n) => n.id === address)?.evidence_ids[0] ?? "",
              )
            }
            centerLabel="합성 근거 보기"
          />
          <ul aria-label="합성 리소스 목록">
            {run.nodes?.map((n) => (
              <li key={n.id}>
                <button
                  className="min-h-11 underline"
                  onClick={() => setSelected(n.evidence_ids[0])}
                >
                  {n.resource_type} · {n.id} 근거
                </button>
              </li>
            ))}
          </ul>
          {!run.nodes?.length && (
            <p>관측 리소스 없음 — 성공한 리소스를 임의로 채우지 않습니다.</p>
          )}
          <aside aria-label="비밀 제거된 이벤트 근거">
            <h3>구조화된 근거 (원문 로그 없음)</h3>
            {run.evidence
              ?.filter((e) => !selected || e.id === selected)
              .map((e) => (
                <p key={e.id}>
                  {e.id}: {e.code} · 비밀값 제외
                </p>
              ))}
          </aside>
        </>
      )}
    </section>
  );
}
