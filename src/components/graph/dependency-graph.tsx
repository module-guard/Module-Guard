"use client";
import { useState } from "react";
import { api, unwrap } from "@/api/client";
import { useApi } from "@/api/hooks";
import { Panel } from "../live-shared";
import { GraphCanvas } from "./graph-canvas";
import type { components } from "@/api/generated/schema";
import type { Direction } from "./types";
/** Single endpoint boundary; neighborhood() is the equivalent full-graph adapter. */
export const loadNeighborhood = (
  id: string,
  address: string,
  depth: number,
  direction: Direction,
  edgeTypes: string[],
  signal: AbortSignal,
) =>
  unwrap(
    api.GET("/api/scans/{scan_id}/graph/neighborhood", {
      signal,
      params: {
        path: { scan_id: id },
        query: {
          address,
          depth,
          direction,
          edge_type: edgeTypes.length ? edgeTypes : undefined,
          limit: 301,
        },
      },
    }),
  );
export function DependencyGraph({ id }: { id: string }) {
  const graph = useApi(
    (s) =>
      unwrap(
        api.GET("/api/scans/{scan_id}/graph", {
          signal: s,
          params: { path: { scan_id: id } },
        }),
      ),
    id,
  );
  return (
    <Panel title="Dependency Graph · 읽기 전용">
      {graph.data ? (
        <NeighborhoodControls key={id} id={id} graph={graph.data} />
      ) : (
        <GraphCanvas
          loading={!graph.error}
          error={graph.error}
          retry={graph.retry}
        />
      )}
    </Panel>
  );
}
function NeighborhoodControls({
  id,
  graph,
}: {
  id: string;
  graph: components["schemas"]["GraphResponse"];
}) {
  const [scope, setScope] = useState<"near" | "all">("near");
  const [center, setCenter] = useState(
    graph.nodes.find((n) => n.type === "ROOT_MODULE")?.address ??
      graph.nodes[0]?.address ??
      "",
  );
  const [search, setSearch] = useState(center);
  const [depth, setDepth] = useState(1);
  const [direction, setDirection] = useState<Direction>("both");
  const types = [...new Set(graph.edges.map((e) => e.type))].sort();
  const [selected, setSelected] = useState(types);
  const r = useApi(
    (s) =>
      center
        ? loadNeighborhood(id, center, depth, direction, selected, s)
        : Promise.resolve({
            schema_version: 1,
            center: "",
            depth,
            direction,
            nodes: [],
            edges: [],
            truncated: false,
          }),
    JSON.stringify([id, center, depth, direction, selected]),
  );
  const changeCenter = (address: string) => {
    setScope("near");
    setCenter(address);
    setSearch(address);
  };
  return (
    <>
      <div
        className="my-3 flex gap-2"
        role="group"
        aria-label="의존성 표시 범위"
      >
        <button
          className="rounded border px-3 py-2"
          aria-pressed={scope === "near"}
          onClick={() => setScope("near")}
        >
          주변 탐색
        </button>
        <button
          className="rounded border px-3 py-2"
          aria-pressed={scope === "all"}
          onClick={() => setScope("all")}
        >
          전체 그래프 (고립 노드 포함)
        </button>
      </div>
      {scope === "near" && (
        <form
          className="flex flex-wrap gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (graph.nodes.some((n) => n.address === search))
              changeCenter(search);
          }}
        >
          <label>
            중심 주소
            <input
              className="input"
              aria-label="중심 주소"
              list={`addresses-${id}`}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                if (graph.nodes.some((n) => n.address === e.target.value))
                  setCenter(e.target.value);
              }}
            />
            <datalist id={`addresses-${id}`}>
              {graph.nodes.map((n) => (
                <option key={n.id} value={n.address} />
              ))}
            </datalist>
          </label>
          <button type="submit">중심 변경</button>
          <label>
            깊이
            <select
              className="input"
              aria-label="그래프 깊이"
              value={depth}
              onChange={(e) => setDepth(Number(e.target.value))}
            >
              {[1, 2, 3].map((n) => (
                <option key={n}>{n}</option>
              ))}
            </select>
          </label>
          <label>
            방향
            <select
              className="input"
              aria-label="그래프 방향"
              value={direction}
              onChange={(e) => setDirection(e.target.value as Direction)}
            >
              <option value="both">양방향</option>
              <option value="out">의존하는 대상 (out)</option>
              <option value="in">의존하는 선언 (in)</option>
            </select>
          </label>
        </form>
      )}
      <fieldset className="my-3 flex flex-wrap gap-3">
        <legend>연결선 타입</legend>
        {types.map((t) => (
          <label key={t} className="text-xs">
            <input
              type="checkbox"
              checked={selected.includes(t)}
              onChange={(e) =>
                setSelected(
                  e.target.checked
                    ? [...selected, t]
                    : selected.filter((x) => x !== t),
                )
              }
            />
            {t}
          </label>
        ))}
      </fieldset>
      <p className="my-2 text-xs text-slate-500">
        선언 → 의존 대상 · 노드를 더블클릭하여 중심 변경
      </p>
      <p className="my-2 text-sm">
        전체 {graph.nodes.length}개 노드 / {graph.edges.length}개 연결 ·{" "}
        {scope === "all"
          ? "모든 노드와 선택한 타입의 연결 표시"
          : `중심의 ${depth}단계 범위 표시. 다른 선언은 중심 주소 검색으로 탐색하세요.`}
      </p>
      {scope === "near" && r.data?.truncated && (
        <p role="status">일부 노드가 생략되었습니다. 범위를 좁혀 주세요.</p>
      )}
      <GraphCanvas
        key={scope}
        nodes={
          scope === "all"
            ? graph.nodes
            : selected.length
              ? r.data?.nodes
              : graph.nodes.filter((n) => n.address === center)
        }
        edges={
          scope === "all"
            ? graph.edges.filter((e) => selected.includes(e.type))
            : selected.length
              ? r.data?.edges
              : []
        }
        loading={scope === "near" && !r.data && !r.error}
        error={scope === "near" ? r.error : undefined}
        retry={r.retry}
        onCenter={changeCenter}
      />
    </>
  );
}
