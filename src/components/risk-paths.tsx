"use client";
import { useState } from "react";
import { api, unwrap } from "@/api/client";
import { useApi } from "@/api/hooks";
import type { components } from "@/api/generated/schema";
import { GraphCanvas } from "./graph/graph-canvas";
import { State } from "./live-shared";
export function RiskPaths({ id }: { id: string }) {
  const [severity, setSeverity] = useState<components["schemas"]["Severity"]>(),
    [rule, setRule] = useState(""),
    [resource, setResource] = useState(""),
    [offset, setOffset] = useState(0),
    [selected, setSelected] = useState("");
  const r = useApi(
    (signal) =>
      unwrap(
        api.GET("/api/scans/{scan_id}/risk-paths", {
          params: {
            path: { scan_id: id },
            query: {
              severity,
              rule: rule || undefined,
              resource: resource || undefined,
              offset,
            },
          },
          signal,
        }),
      ),
    `${id}/${severity}/${rule}/${resource}/${offset}`,
  );
  const paths = r.data?.paths ?? [],
    chosen = paths.find((p) => p.finding_id === selected) ?? paths[0];
  return (
    <section aria-label="정적 위험 경로" className="space-y-4">
      <h2 className="text-xl font-bold">위험 경로·코드 근거</h2>
      <p>
        정적 추론이며 실제 실행·통신을 관측한 결과가 아닙니다. 민감값 내용은
        표시하지 않습니다.
      </p>
      <div className="flex flex-wrap gap-3">
        <label>
          심각도{" "}
          <select
            value={severity ?? ""}
            onChange={(e) => {
              setSeverity((e.target.value || undefined) as typeof severity);
              setOffset(0);
            }}
          >
            {["", "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO", "UNKNOWN"].map(
              (v) => (
                <option key={v} value={v}>
                  {v || "전체"}
                </option>
              ),
            )}
          </select>
        </label>
        <label>
          규칙{" "}
          <input
            className="border p-2"
            value={rule}
            onChange={(e) => {
              setRule(e.target.value);
              setOffset(0);
            }}
          />
        </label>
        <label>
          리소스 주소{" "}
          <input
            className="border p-2"
            value={resource}
            onChange={(e) => {
              setResource(e.target.value);
              setOffset(0);
            }}
          />
        </label>
      </div>
      {!r.data ? (
        <State {...r} />
      ) : (
        <>
          {r.data.incomplete && (
            <p role="status">
              부분 분석 또는 분석 상태 미확인 — 경로 누락 가능성이 있습니다.
            </p>
          )}
          <ul aria-label="위험 경로 목록">
            {paths.map((p) => (
              <li key={p.finding_id}>
                <button
                  className="min-h-11 text-brand underline"
                  onClick={() => setSelected(p.finding_id)}
                >
                  {p.severity} · {p.rule} ·{" "}
                  {p.location.address ?? "주소 미확인"}
                </button>
              </li>
            ))}
          </ul>
          {!paths.length && (
            <p>이 조건의 진단 근거가 없습니다. 안전성을 보증하지 않습니다.</p>
          )}
          {chosen && (
            <>
              <p>{chosen.interpretation}</p>
              <GraphCanvas
                label="선택한 위험 근거 연결"
                nodes={chosen.nodes}
                edges={chosen.edges?.map((e) => ({
                  ...e,
                  target: e.target ?? null,
                  metadata: { resolved: e.resolved, basis: e.basis },
                }))}
              />
              <aside
                aria-label="코드 근거"
                className="rounded border p-3 break-all"
              >
                <h3>
                  {chosen.rule} · {chosen.severity}
                </h3>
                <p>
                  소스: {chosen.location.file ?? "파일 미확인"}:
                  {chosen.location.line ?? "줄 미확인"}
                </p>
                <p>리소스: {chosen.location.address}</p>
                <p>권장 조치 근거: {chosen.recommendation ?? "기록 없음"}</p>
                {chosen.truncated && <p>주변 연결 표시 상한에 도달했습니다.</p>}
              </aside>
              <ul aria-label="위험 연결 표 대체 보기">
                {chosen.edges?.map((e) => (
                  <li key={e.id}>
                    {e.type} ·{" "}
                    {e.basis === "rule_evidence"
                      ? "규칙이 인용한 근거"
                      : "정적 인접 연결"}{" "}
                    · {e.resolved ? "대상 해석됨" : "대상 해석 불가"}
                  </li>
                ))}
              </ul>
            </>
          )}
          <button
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            이전 위험
          </button>{" "}
          ·{" "}
          <button
            disabled={r.data.next_offset == null}
            onClick={() => setOffset(r.data!.next_offset!)}
          >
            다음 위험
          </button>
        </>
      )}
    </section>
  );
}
