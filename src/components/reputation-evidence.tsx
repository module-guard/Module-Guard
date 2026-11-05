"use client";
import { useState } from "react";
import { api, unwrap } from "@/api/client";
import { useApi } from "@/api/hooks";
import { State } from "./live-shared";
const states: Record<string, string> = {
  confirmed: "데이터 확인됨",
  insufficient: "정보 부족",
  unverified: "미확인",
  failed: "수집 실패",
  not_applicable: "해당 없음",
  stale: "오래된 자료",
};
const axes: Record<string, string> = {
  security: "보안 운영",
  maintenance: "유지보수",
  provenance: "출처 추적",
  documentation: "문서",
  tests: "테스트",
  popularity: "인기도",
};
export function ReputationEvidence({
  owner,
  name,
  version,
  canCollect,
}: {
  owner: string;
  name: string;
  version: string;
  canCollect: boolean;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [axis, setAxis] = useState(""),
    [offset, setOffset] = useState(0);
  const r = useApi(
    (signal) =>
      unwrap(
        api.GET("/api/repositories/{owner}/{name}/reputation", {
          params: { path: { owner, name }, query: { version, offset } },
          signal,
        }),
      ),
    `${owner}/${name}/${version}/${offset}`,
  );
  const latest = r.data?.entries?.[0];
  async function collect() {
    setBusy(true);
    setError("");
    try {
      await unwrap(
        api.POST("/api/repositories/{owner}/{name}/reputation/collect", {
          params: { path: { owner, name }, query: { version } },
        }),
      );
      setOffset(0);
      r.retry();
    } catch (e) {
      setError(e instanceof Error ? e.message : "수집 실패");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="평판 근거" className="space-y-4">
      <h2 className="text-xl font-bold">평판 근거와 변화</h2>
      <p>
        독립 평가가 완료되지 않아 총점을 제공하지 않습니다. 평판은 서명 실패나
        중대한 보안 위험을 상쇄하지 않습니다.
      </p>
      {canCollect && (
        <button
          disabled={busy}
          onClick={collect}
          className="rounded border p-3"
        >
          {busy ? "수집 중…" : "공개 평판 근거 수집"}
        </button>
      )}
      {error && <p role="alert">{error}</p>}
      {!r.data ? (
        <State {...r} />
      ) : !latest ? (
        <p>수집된 근거가 없습니다. 미확인은 0점이나 안전을 뜻하지 않습니다.</p>
      ) : (
        <>
          <p>{latest.notice}</p>
          <p>
            정책 {latest.policy_version} · 수집{" "}
            <time>{latest.collected_at}</time>
          </p>
          <label>
            개인 보기 필터 (평가 정책은 유지){" "}
            <select value={axis} onChange={(e) => setAxis(e.target.value)}>
              <option value="">전체 영역</option>
              {Object.entries(axes).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <div aria-label="영역별 근거 충족률" className="space-y-3">
            {Object.entries(latest.coverage ?? {})
              .filter(([k]) => !axis || k === axis)
              .map(([k, v]) => (
                <div key={k}>
                  <label>
                    {axes[k]}: {v}%{" "}
                    <progress
                      max={100}
                      value={v}
                      aria-label={`${axes[k]} 근거 충족률`}
                    />
                  </label>
                </div>
              ))}
          </div>
          <p>
            계산식: 확인된 항목 수 ÷ 해당 없음 제외 항목 수 × 100. false·0도
            확인된 원자료이며 좋은 평가를 뜻하지 않습니다. 7일이 지난 자료는
            아래 상태를 확인하세요.
          </p>
          <ul>
            {(latest.metrics ?? [])
              .filter((m) => !axis || m.axis === axis)
              .map((m) => (
                <li key={m.id} className="rounded border p-3">
                  <details>
                    <summary>
                      {axes[m.axis]} · {m.id} · {states[m.status]} ·{" "}
                      {m.value == null ? "측정값 없음" : String(m.value)}
                    </summary>
                    <p>{m.formula}</p>
                    {m.reason && <p>{m.reason}</p>}
                    {m.source_url ? (
                      <a
                        className="text-brand underline break-all"
                        href={m.source_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        공개 원자료
                      </a>
                    ) : (
                      <p>수집 manifest 근거 또는 외부 출처 없음</p>
                    )}
                  </details>
                </li>
              ))}
          </ul>
          <h3>선택 버전의 평가 이력</h3>
          <div className="overflow-auto">
            <table className="w-full">
              <caption>근거 충족률 변화 · 같은 정책에서만 비교하세요</caption>
              <thead>
                <tr>
                  <th>시각</th>
                  <th>정책</th>
                  {Object.entries(axes)
                    .filter(([k]) => k !== "popularity")
                    .map(([k, v]) => (
                      <th key={k}>{v}</th>
                    ))}
                </tr>
              </thead>
              <tbody>
                {r.data.entries?.map((e) => (
                  <tr key={e.collected_at}>
                    <td>{e.collected_at}</td>
                    <td>{e.policy_version}</td>
                    {Object.keys(axes)
                      .filter((k) => k !== "popularity")
                      .map((k) => (
                        <td key={k}>{e.coverage?.[k] ?? "미확인"}%</td>
                      ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            이전 이력
          </button>{" "}
          ·{" "}
          <button
            disabled={r.data.next_offset == null}
            onClick={() => setOffset(r.data!.next_offset!)}
          >
            다음 이력
          </button>
        </>
      )}
    </section>
  );
}
