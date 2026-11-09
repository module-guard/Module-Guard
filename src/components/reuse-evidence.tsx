"use client";
import Link from "next/link";
import { api, unwrap } from "@/api/client";
import { useApi } from "@/api/hooks";
import { GraphCanvas } from "./graph/graph-canvas";
import { State } from "./live-shared";
export function ReuseEvidence({
  owner,
  name,
  version,
}: {
  owner: string;
  name: string;
  version: string;
}) {
  const r = useApi(
    (signal) =>
      unwrap(
        api.GET("/api/repositories/{owner}/{name}/trust/reuse", {
          params: { path: { owner, name }, query: { version } },
          signal,
        }),
      ),
    `${owner}/${name}/${version}`,
  );
  const rows = r.data?.records ?? [];
  const reused = rows.filter((r) => r.reused && r.source_scan_id);
  const ids = [
    ...new Set(reused.flatMap((r) => [r.target_scan_id, r.source_scan_id!])),
  ];
  return (
    <section aria-label="검사 재사용" className="space-y-3">
      <h2 className="text-xl font-bold">검사 재사용 근거</h2>
      <p>검사 재사용 간선은 모듈의 복사·파생 관계와 다릅니다.</p>
      {!r.data ? (
        <State {...r} />
      ) : (
        <>
          <p>{r.data.metric_note}</p>
          <p>
            이 검사에서 기록된 재사용: {rows.filter((r) => r.reused).length}회
          </p>
          {!rows.length && (
            <p>
              재사용 판단 기록이 없습니다. 과거 검사에 소급 적용하지 않습니다.
            </p>
          )}
          <GraphCanvas
            label="검사 재사용 관계"
            nodes={ids.map((id) => ({ id, address: id, type: "검사" }))}
            edges={reused.map((r) => ({
              id: r.id,
              source: r.source_scan_id!,
              target: r.target_scan_id,
              type: "검사 결과 재사용",
            }))}
          />
          <ul>
            {rows.map((row) => (
              <li key={row.id} className="border p-3 break-all">
                <strong>
                  {row.reused ? "검사 결과 재사용" : "새 검사 필요"}
                </strong>
                <p>
                  판단 사유: {row.reason} · 정책: {row.policy_version}
                </p>
                <p>판단 시각: {row.created_at}</p>
                <p>
                  현재 원검사 증명: {row.current_source_status}
                  {row.reused && row.current_source_status !== "valid"
                    ? " · 재검사 또는 증명 확인 필요"
                    : ""}
                </p>
                <p>조건 키: {row.key ?? "미확인"}</p>
                <p>
                  원검사 시간:{" "}
                  {row.source_duration_ms == null
                    ? "측정 기록 없음"
                    : `${row.source_duration_ms}ms`}{" "}
                  · 재사용 조회 시간: {row.lookup_duration_ms}ms
                </p>
                {row.source_scan_id && (
                  <Link href={`/scans/${row.source_scan_id}`}>원검사 상세</Link>
                )}{" "}
                ·{" "}
                <Link href={`/scans/${row.target_scan_id}`}>
                  대상 검사 상세
                </Link>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
