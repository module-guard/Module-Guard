"use client";
import { ReuseEvidence } from "./reuse-evidence";
import { useState } from "react";
import Link from "next/link";
import { api, unwrap } from "@/api/client";
import { useApi } from "@/api/hooks";
import type { components } from "@/api/generated/schema";
import { GraphCanvas } from "./graph/graph-canvas";
import { State } from "./live-shared";

type Node = components["schemas"]["LineageNode"];
const href = (n: Node) =>
  `/${encodeURIComponent(n.ref.owner)}/${encodeURIComponent(n.ref.name)}?version=${encodeURIComponent(n.ref.version ?? "")}&tab=trust`;
const relationWords: Record<string, string> = {
  ORIGINAL: "원본",
  VERSION: "새 버전",
  FORK: "복사·파생",
};
const comparisonWords: Record<string, string> = {
  IDENTICAL: "파일 동일",
  CHANGED: "파일 변경",
  NOT_COMPARABLE: "비교 불가",
};
export function TrustExplorer({
  owner,
  name,
  version,
  versions,
}: {
  owner: string;
  name: string;
  version: string;
  versions: string[];
}) {
  const [direction, setDirection] = useState<"ancestors" | "descendants">(
    "ancestors",
  );
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [relation, setRelation] = useState("");
  const [selected, setSelected] = useState("");
  const path = { owner, name };
  const root = useApi(
    (signal) =>
      unwrap(
        api.GET("/api/repositories/{owner}/{name}/lineage", {
          params: { path, query: { version } },
          signal,
        }),
      ),
    `${owner}/${name}/${version}`,
  );
  const graph = useApi(
    (signal) =>
      unwrap(
        api.GET("/api/repositories/{owner}/{name}/lineage/graph", {
          params: { path, query: { version, direction, offset, limit: 20 } },
          signal,
        }),
      ),
    `${owner}/${name}/${version}/${direction}/${offset}`,
  );
  const diff = useApi(
    (signal) =>
      unwrap(
        api.GET("/api/repositories/{owner}/{name}/lineage/diff", {
          params: { path, query: { version } },
          signal,
        }),
      ),
    `${owner}/${name}/${version}`,
  );
  const all = [
    ...new Map(
      [...(root.data ? [root.data] : []), ...(graph.data?.nodes ?? [])].map(
        (n) => [n.ref.id, n],
      ),
    ).values(),
  ];
  const nodes = all.filter(
    (n) =>
      (!relation || n.relation === relation) &&
      `${n.ref.owner}/${n.ref.name} ${n.ref.version}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const ids = new Set(nodes.map((n) => n.ref.id));
  const chosen = all.find((n) => n.ref.id === selected);
  return (
    <div className="space-y-6">
      <section aria-label="신뢰 계보" className="space-y-3">
        <h2 className="text-xl font-bold">출처·계보 탐색</h2>
        <p>
          파일 동일성, 서명 유효성, 보안 위험은 별개의 근거입니다. 게시자는 코드
          작성자와 다를 수 있습니다.
        </p>
        <div className="flex flex-wrap gap-3">
          <label>
            탐색 방향{" "}
            <select
              value={direction}
              onChange={(e) => {
                setDirection(e.target.value as typeof direction);
                setOffset(0);
              }}
            >
              <option value="ancestors">원본 방향</option>
              <option value="descendants">파생 방향</option>
            </select>
          </label>
          <label>
            노드 검색{" "}
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="border p-2"
            />
          </label>
          <label>
            관계{" "}
            <select
              value={relation}
              onChange={(e) => setRelation(e.target.value)}
            >
              <option value="">전체 관계</option>
              {Object.entries(relationWords).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
        </div>
        {!root.data ? (
          <State {...root} />
        ) : !graph.data ? (
          <State {...graph} />
        ) : (
          <>
            <GraphCanvas
              label="원본·파생 신뢰 그래프"
              nodes={nodes.map((n) => ({
                id: n.ref.id,
                address: `${n.ref.owner}/${n.ref.name}@${n.ref.version}`,
                type: n.relation,
                metadata: { comparison: comparisonWords[n.comparison.status] },
              }))}
              edges={graph.data.edges
                .filter((e) => ids.has(e.parent) && ids.has(e.child))
                .map((e) => ({
                  id: `${e.parent}:${e.child}`,
                  source: e.parent,
                  target: e.child,
                  type: relationWords[e.relation],
                }))}
              onCenter={(address) =>
                setSelected(
                  nodes.find(
                    (n) =>
                      `${n.ref.owner}/${n.ref.name}@${n.ref.version}` ===
                      address,
                  )?.ref.id ?? "",
                )
              }
              centerLabel="계보 근거 보기"
            />
            <ul aria-label="계보 목록 대체 보기" className="divide-y">
              {nodes.map((n) => (
                <li key={n.ref.id} className="flex flex-wrap gap-3 py-3">
                  <button
                    className="text-brand underline min-h-11"
                    onClick={() => setSelected(n.ref.id)}
                  >
                    {n.ref.owner}/{n.ref.name}@{n.ref.version}
                  </button>
                  <span>
                    {relationWords[n.relation]} ·{" "}
                    {comparisonWords[n.comparison.status]}
                  </span>
                  <Link href={href(n)}>모듈로 이동</Link>
                </li>
              ))}
            </ul>
            {!nodes.length && <p>표시할 노드가 없습니다.</p>}
            <div className="flex gap-4">
              <button
                disabled={!offset}
                onClick={() => setOffset(Math.max(0, offset - 20))}
              >
                이전 계보
              </button>
              <button
                disabled={graph.data.next_offset == null}
                onClick={() => setOffset(graph.data!.next_offset!)}
              >
                다음 계보
              </button>
              <span>
                접근 가능한 노드 {graph.data.total}개
                {graph.data.truncated ? " · 탐색 상한 도달" : ""}
              </span>
            </div>
          </>
        )}
        {chosen && (
          <aside
            aria-label="계보 상세"
            className="rounded border p-4 break-all"
          >
            <h3>
              {chosen.ref.name}@{chosen.ref.version}
            </h3>
            <p>
              게시자: {chosen.publisher.login ?? "기록 없음"} ·{" "}
              {chosen.published_at}
            </p>
            <p>SHA-256: {chosen.manifest_sha256 ?? "기록 없음"}</p>
            <p>
              원본 commit: {chosen.source.commit_hash ?? "미확인"} · 경로:{" "}
              {chosen.source.module_path}
            </p>
            <p>
              파일 추가 {chosen.comparison.added} · 삭제{" "}
              {chosen.comparison.removed} · 수정 {chosen.comparison.modified}
            </p>
            <Link href={href(chosen)}>이 버전의 검사·서명 근거 열기</Link>
          </aside>
        )}
        {diff.data && (
          <details>
            <summary>선택 버전과 부모의 파일 변경 ({diff.data.total})</summary>
            <ul>
              {(diff.data.files ?? []).map((f) => (
                <li key={f.path}>
                  {f.change}: {f.path}
                </li>
              ))}
            </ul>
            {diff.data.next_offset != null && <p>첫 50개 파일만 표시합니다.</p>}
          </details>
        )}
      </section>
      <ReuseEvidence owner={owner} name={name} version={version} />
      <VersionCompare
        owner={owner}
        name={name}
        version={version}
        versions={versions}
      />
      <VerificationTimeline owner={owner} name={name} version={version} />
    </div>
  );
}

export function VersionCompare({
  owner,
  name,
  version,
  versions,
}: {
  owner: string;
  name: string;
  version: string;
  versions: string[];
}) {
  const [before, setBefore] = useState(
    versions.find((v) => v !== version) ?? version,
  );
  const [after, setAfter] = useState(version);
  const [offset, setOffset] = useState(0);
  const [filter, setFilter] = useState("");
  const r = useApi(
    (signal) =>
      unwrap(
        api.GET("/api/repositories/{owner}/{name}/trust/compare", {
          params: { path: { owner, name }, query: { before, after, offset } },
          signal,
        }),
      ),
    `${owner}/${name}/${before}/${after}/${offset}`,
  );
  const changes =
    r.data?.changes?.filter((c) => !filter || c.change === filter) ?? [];
  return (
    <section className="space-y-3" aria-label="버전 비교">
      <h2 className="text-xl font-bold">버전별 리소스·위험 비교</h2>
      <div className="flex flex-wrap gap-4">
        {(["A", "B"] as const).map((side) => (
          <label key={side}>
            버전 {side}{" "}
            <select
              value={side === "A" ? before : after}
              onChange={(e) => {
                (side === "A" ? setBefore : setAfter)(e.target.value);
                setOffset(0);
              }}
            >
              {versions.map((v) => (
                <option key={v}>{v}</option>
              ))}
            </select>
          </label>
        ))}
        <label>
          변경 필터{" "}
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            {["", "added", "removed", "modified", "unchanged", "uncertain"].map(
              (v) => (
                <option key={v} value={v}>
                  {v || "전체"}
                </option>
              ),
            )}
          </select>
        </label>
      </div>
      {!r.data ? (
        <State {...r} />
      ) : (
        <>
          <p>{r.data.interpretation}</p>
          <p>
            원본 파일:{" "}
            {r.data.manifest_equal == null
              ? "확인 불가"
              : r.data.manifest_equal
                ? "동일"
                : "다름"}{" "}
            · 분석 조건:{" "}
            {r.data.tool_conditions_equal ? "동일" : "상이하거나 미확인"}
          </p>
          {(r.data.reasons ?? []).map((x) => (
            <p key={x} role="status">
              {x === "INCOMPLETE_ANALYSIS"
                ? "불완전한 분석 — 위험 감소를 확정할 수 없습니다."
                : "도구·규칙 조건이 다르거나 기록이 부족합니다."}
            </p>
          ))}
          <div className="flex flex-wrap gap-3">
            {Object.entries(r.data.counts ?? {}).map(([k, v]) => (
              <span className="rounded border p-2" key={k}>
                {k}: {v}
              </span>
            ))}
          </div>
          <GraphCanvas
            label="버전 변경 리소스"
            nodes={changes
              .filter((c) => c.kind === "resource")
              .map((c) => ({
                id: c.key,
                address: c.after?.address ?? c.before?.address ?? c.key,
                type: c.change,
                file_path: c.after?.file ?? c.before?.file,
                line_start: c.after?.line ?? c.before?.line,
              }))}
          />
          <ul aria-label="버전 변경 목록">
            {changes.map((c) => (
              <li
                className="border-b py-2 break-all"
                key={`${c.kind}:${c.key}`}
              >
                <details>
                  <summary>
                    {c.kind} · {c.change} ·{" "}
                    {c.after?.address ?? c.before?.address ?? c.key}
                  </summary>
                  <p>
                    이전: {c.before?.file}:{c.before?.line} ·{" "}
                    {c.before?.severity}
                  </p>
                  <p>
                    이후: {c.after?.file}:{c.after?.line} · {c.after?.severity}{" "}
                    · {c.after?.rule ?? c.before?.rule}
                  </p>
                  {r.data?.after_scan && (
                    <Link href={`/scans/${r.data.after_scan}`}>
                      검사 근거 확인
                    </Link>
                  )}
                </details>
              </li>
            ))}
          </ul>
          <button
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 50))}
          >
            이전 변경
          </button>{" "}
          ·{" "}
          <button
            disabled={r.data.next_offset == null}
            onClick={() => setOffset(r.data!.next_offset!)}
          >
            다음 변경
          </button>
        </>
      )}
    </section>
  );
}
export function VerificationTimeline({
  owner,
  name,
  version,
}: {
  owner: string;
  name: string;
  version: string;
}) {
  const [filter, setFilter] = useState("");
  const r = useApi(
    (signal) =>
      unwrap(
        api.GET("/api/repositories/{owner}/{name}/trust/timeline", {
          params: { path: { owner, name }, query: { version } },
          signal,
        }),
      ),
    `${owner}/${name}/${version}`,
  );
  return (
    <section className="space-y-3" aria-label="검증 타임라인">
      <h2 className="text-xl font-bold">검증 타임라인</h2>
      {!r.data ? (
        <State {...r} />
      ) : (
        <>
          <p>
            현재 서명 상태: {r.data.signature_status} · 조회 시각{" "}
            {r.data.observed_at}
          </p>
          <p>서명이 유효해도 보안 위험이 없다는 뜻은 아닙니다.</p>
          <label>
            이벤트 검색{" "}
            <input
              className="border p-2"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
          </label>
          <ol>
            {(r.data.events ?? [])
              .filter((e) => `${e.stage} ${e.status}`.includes(filter))
              .map((e) => (
                <li key={e.id} className="border-l-2 p-3 break-all">
                  <strong>
                    {e.stage} · {e.status}
                  </strong>
                  <p>
                    <time>{e.at}</time> · {e.time_basis}
                  </p>
                  <p>주체: {e.actor ?? "기록 없음"}</p>
                  <p>대상 digest: {e.digest ?? "기록 없음"}</p>
                  {e.evidence && <Link href={e.evidence}>검사·시도 이력</Link>}
                </li>
              ))}
          </ol>
          {!r.data.events?.length && <p>기록된 이벤트가 없습니다.</p>}
          <p>시각 기록 없음: {r.data.unrecorded?.join(", ")}</p>
        </>
      )}
    </section>
  );
}
