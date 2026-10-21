"use client";
import { useMemo, useState, useEffect } from "react";
import {
  ReactFlow,
  useNodesState,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  type NodeProps,
  type Edge,
  type EdgeProps,
  type ReactFlowInstance,
  BaseEdge,
  getSmoothStepPath,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { prepareGraph } from "./normalize";
import { useGraphLayout } from "./use-graph-layout";
import type { VisualNode, VisualEdge } from "./types";
export const nodeColors: Record<string, string> = {
  ROOT_MODULE: "#7c3aed",
  MODULE: "#6366f1",
  RESOURCE: "#dc2626",
  DATA: "#0891b2",
  VARIABLE: "#16a34a",
  LOCAL: "#ca8a04",
  OUTPUT: "#db2777",
  PROVIDER: "#0d9488",
  REQUIRED_PROVIDER: "#0d9488",
  UNRESOLVED: "#64748b",
  SOURCE_TRUST: "#b45309",
  FLOW: "#2563eb",
  SINK: "#dc2626",
  CHECKOV: "#9333ea",
  TFCR: "#be123c",
};
const dashed = (m?: Record<string, unknown>) =>
  m?.opaque === true || m?.external === true || m?.resolved === false;
export function CategoryBadges({
  metadata: m,
}: {
  metadata?: Record<string, unknown>;
}) {
  const badges = [
    ...(Array.isArray(m?.source_categories)
      ? m.source_categories
      : [m?.source_category]
    ).map((value) => ({ value, sink: false })),
    ...(Array.isArray(m?.sink_categories)
      ? m.sink_categories
      : [m?.sink_category]
    ).map((value) => ({ value, sink: true })),
  ].filter((b) => b.value && b.value !== "UNKNOWN");
  return (
    <span className="flex flex-wrap gap-1">
      {badges.map((b, i) => (
        <span
          key={i}
          className={`rounded px-1 text-xs ${b.sink ? "bg-red-100 font-bold text-red-800" : "bg-blue-100 text-blue-800"}`}
        >
          {String(b.value)}
        </span>
      ))}
    </span>
  );
}
function EntityNode({ data, selected }: NodeProps) {
  const n = data.node as VisualNode;
  return (
    <div
      title={`${n.address}\n${n.file_path ?? ""}${n.line_start != null ? `:${n.line_start}` : ""}`}
      className="h-full rounded-lg border-2 bg-white p-3 shadow-sm"
      style={{
        borderColor: nodeColors[n.type] ?? "#7c3aed",
        borderStyle:
          n.type === "UNRESOLVED" || dashed(n.metadata) ? "dashed" : "solid",
        outline: selected ? "3px solid #c4b5fd" : undefined,
      }}
    >
      <Handle type="target" position={Position.Left} />
      <span className="text-xs" style={{ color: nodeColors[n.type] }}>
        {n.type}
      </span>
      <div className="line-clamp-2 break-words text-base font-semibold leading-5">
        {String(n.metadata?.entity_address || n.address)
          .split(".")
          .slice(-2)
          .join(".")
          .split(/(?<=[._])/)
          .map((part, i) => (
            <span key={i}>
              {part}
              <wbr />
            </span>
          ))}
      </div>
      {n.metadata?.attribute_path != null && (
        <div className="truncate text-xs text-slate-500">
          속성 · {String(n.metadata.attribute_path)}
        </div>
      )}
      {Number(n.metadata?.attribute_count) > 1 && (
        <div className="text-xs text-slate-500">
          흐름 속성 {String(n.metadata?.attribute_count)}개 · 상세에서 펼치기
        </div>
      )}
      <div className="mt-2">
        <CategoryBadges metadata={n.metadata} />
      </div>
      {n.metadata?.rule != null && (
        <div className="text-xs font-mono">{String(n.metadata.rule)}</div>
      )}
      {n.file_path && (
        <div className="mt-1 truncate text-xs text-slate-500">
          {n.file_path}
          {n.line_start != null ? `:${n.line_start}` : ""}
        </div>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes = {
  entity: EntityNode,
  moduleGroup: ({ data }: NodeProps) => (
    <div className="p-2 text-xs text-slate-500">{String(data.label)}</div>
  ),
};
export function edgeStyle(e: VisualEdge) {
  return {
    stroke:
      e.type === "USES_PROVIDER"
        ? "#cbd5e1"
        : /REFERENCES|CONSUMES_MODULE_OUTPUT/.test(e.type)
          ? "#7c3aed"
          : "#64748b",
    strokeWidth: e.type === "CALLS_MODULE" ? 3 : 1.5,
    strokeDasharray:
      e.type.endsWith("_DEPENDS_ON") || dashed(e.metadata) ? "6 4" : undefined,
  };
}
function RoutedEdge(props: EdgeProps) {
  const { sourceX: sx, sourceY: sy, targetX: tx, targetY: ty, data } = props;
  const lane = Number(data?.lane ?? 0);
  const path =
    props.source === props.target
      ? `M ${sx} ${sy} C ${sx + 80 + lane} ${sy - 160} ${tx - 80 - lane} ${ty - 160} ${tx} ${ty}`
      : lane
        ? `M ${sx} ${sy} C ${sx + Math.max(80, Math.abs(tx - sx) / 2)} ${sy + lane} ${tx - Math.max(80, Math.abs(tx - sx) / 2)} ${ty + lane} ${tx} ${ty}`
        : getSmoothStepPath(props)[0];
  return (
    <BaseEdge
      id={props.id}
      path={path}
      style={props.style}
      markerEnd={props.markerEnd}
      label={props.label}
      labelX={(sx + tx) / 2}
      labelY={(sy + ty) / 2 + lane * 0.75}
    />
  );
}
const edgeTypes = { routed: RoutedEdge };
const EMPTY_NODES: VisualNode[] = [];
const EMPTY_EDGES: VisualEdge[] = [];
export function GraphCanvas({
  nodes = EMPTY_NODES,
  edges = EMPTY_EDGES,
  loading,
  error,
  retry,
  onCenter,
  centerLabel = "이 노드 중심으로 보기",
  label = "의존성 그래프",
}: {
  nodes?: VisualNode[];
  edges?: VisualEdge[];
  loading?: boolean;
  error?: string;
  retry?: () => void;
  onCenter?: (address: string) => void;
  centerLabel?: string;
  label?: string;
}) {
  const [selected, setSelected] = useState("");
  const [hover, setHover] = useState("");
  const [selectedEdge, setSelectedEdge] = useState("");
  const [component, setComponent] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [instance, setInstance] = useState<ReactFlowInstance>();
  const [edgeListOpen, setEdgeListOpen] = useState(false);
  const [edgeListLimit, setEdgeListLimit] = useState(100);
  const prepared = useMemo(() => prepareGraph(nodes, edges), [nodes, edges]);
  const { shownNodes, shownEdges } = useMemo(() => {
    const members =
      component && prepared.components.find((c) => c[0] === component);
    const ids = new Set(members || []);
    return {
      shownNodes: members
        ? prepared.nodes.filter((n) => ids.has(n.id))
        : prepared.nodes,
      shownEdges: members
        ? prepared.edges.filter((e) => ids.has(e.source))
        : prepared.edges,
    };
  }, [prepared, component]);
  const { positioned, pending, layoutError, retryLayout } = useGraphLayout(
    shownNodes,
    shownEdges,
  );
  const nodeById = useMemo(
    () => new Map(prepared.nodes.map((n) => [n.id, n])),
    [prepared.nodes],
  );
  const initialFitOptions = useMemo(
    () => ({
      includeHiddenNodes: true,
      nodes: shownNodes.slice(0, 1).map((n) => ({ id: n.id })),
      minZoom: 0.9,
      maxZoom: 0.9,
    }),
    [shownNodes],
  );
  const [flowNodes, setFlowNodes, onNodesChange] = useNodesState(positioned);
  useEffect(() => {
    setFlowNodes(positioned);
  }, [positioned, setFlowNodes]);
  useEffect(() => {
    setFlowNodes((current) =>
      current.map((n) => ({ ...n, selected: n.id === selected })),
    );
  }, [selected, setFlowNodes]);
  const lanes = useMemo(() => {
    const groups = new Map<string, string[]>();
    for (const e of shownEdges) {
      const key = JSON.stringify([e.source, e.target]);
      const group = groups.get(key) ?? [];
      group.push(e.id);
      groups.set(key, group);
    }
    return new Map(
      [...groups.values()].flatMap((group) =>
        group.map(
          (id, i) =>
            [
              id,
              (i - (group.length - 1) / 2) *
                Math.min(64, 320 / Math.max(1, group.length - 1)),
            ] as const,
        ),
      ),
    );
  }, [shownEdges]);
  const visibleEdges = useMemo<Edge[]>(
    () =>
      shownEdges.map((e) => ({
        ...e,
        type: "routed",
        data: { lane: lanes.get(e.id) ?? 0 },
        label: hover === e.id || selectedEdge === e.id ? e.type : undefined,
        style: edgeStyle(e),
        markerEnd: { type: MarkerType.ArrowClosed },
        selected: selectedEdge === e.id,
      })),
    [shownNodes.length, shownEdges, lanes, hover, selectedEdge],
  );
  const detail = shownNodes.find((n) => n.id === selected);
  const edgeDetail = shownEdges.find((e) => e.id === selectedEdge);
  if (error)
    return (
      <div role="alert">
        {error}
        {retry && <button onClick={retry}>다시 시도</button>}
      </div>
    );
  if (loading) return <p role="status">그래프를 불러오는 중…</p>;

  if (!prepared.nodes.length)
    return <p role="status">표시할 그래프 노드가 없습니다.</p>;
  return (
    <section
      className={
        expanded
          ? "fixed inset-3 z-50 overflow-auto rounded-xl border bg-white p-5 shadow-xl"
          : "relative"
      }
    >
      <div className="my-3 flex flex-wrap items-center gap-3 rounded-lg bg-slate-50 p-3 text-sm">
        <strong>
          노드 {shownNodes.length}개 · 연결선 {shownEdges.length}개
        </strong>
        <label>
          연결 그룹{" "}
          <select
            aria-label={`${label} 연결 그룹`}
            className="max-w-full rounded border p-1 sm:max-w-64"
            value={component}
            onChange={(e) => {
              setComponent(e.target.value);
              setSelected("");
              setSelectedEdge("");
            }}
          >
            <option value="">전체 ({prepared.components.length}개 그룹)</option>
            {prepared.components.map((c, i) => (
              <option key={c[0]} value={c[0]}>
                그룹 {i + 1} · {c.length}개 · {nodeById.get(c[0])?.address}
              </option>
            ))}
          </select>
        </label>
        <label>
          노드 찾기{" "}
          <select
            aria-label={`${label} 노드 찾기`}
            className="max-w-full rounded border p-1 sm:max-w-64"
            value={selected}
            onChange={(e) => {
              setSelected(e.target.value);
              setSelectedEdge("");
              if (e.target.value)
                void instance?.fitView({
                  includeHiddenNodes: true,
                  nodes: [{ id: e.target.value }],
                  minZoom: 0.9,
                  maxZoom: 1,
                  duration: 200,
                });
            }}
          >
            <option value="">주소 선택</option>
            {shownNodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.address}
              </option>
            ))}
          </select>
        </label>
        <button
          className="rounded border bg-white px-3 py-1"
          onClick={() =>
            instance?.fitView({
              includeHiddenNodes: true,
              nodes: [{ id: selected || shownNodes[0].id }],
              minZoom: 0.9,
              maxZoom: 0.9,
            })
          }
        >
          읽기 배율
        </button>
        <button
          className="rounded border bg-white px-3 py-1"
          onClick={() =>
            instance?.fitView({
              includeHiddenNodes: true,
              padding: 0.15,
              minZoom: 0.001,
              maxZoom: 1,
            })
          }
        >
          전체 맞춤
        </button>
        <button
          className="ml-auto rounded border bg-white px-3 py-1"
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "크게 보기 닫기" : "크게 보기"}
        </button>
      </div>
      {prepared.missing > 0 && (
        <p role="status" className="my-2 text-amber-700">
          응답에 상세가 없는 노드 {prepared.missing}개를 미해결 노드로
          표시했습니다. 연결선은 유지됩니다.
        </p>
      )}
      <div className="my-3 flex flex-wrap gap-3 text-xs" aria-label="노드 범례">
        {Object.entries(nodeColors)
          .filter(([type]) => shownNodes.some((n) => n.type === type))
          .map(([type, color]) => (
            <span key={type} style={{ color }}>
              {type === "UNRESOLVED" ? "┄" : "●"} {type}
            </span>
          ))}
      </div>
      <p className="mb-2 text-xs text-slate-500">
        드래그로 이동 · 휠로 확대/축소 · 선택하면 상세 표시 · 전체 맞춤은 글자가
        작아질 수 있습니다.
        {shownNodes.length > 150 &&
          " 전체 데이터는 유지하며 현재 화면 안의 노드와 연결만 그립니다."}
      </p>
      {pending ? (
        <p role="status">
          노드 {shownNodes.length}개 배치 계산 중… 화면 조작은 계속할 수
          있습니다.
        </p>
      ) : layoutError ? (
        <p role="alert">
          그래프 배치를 계산하지 못했습니다.{" "}
          <button onClick={retryLayout}>다시 시도</button>
        </p>
      ) : (
        <div className="relative">
          <div
            role="img"
            aria-label={label}
            className="h-[560px] min-w-0 rounded-xl border border-line bg-slate-50/50"
          >
            <ReactFlow
              key={JSON.stringify([
                shownNodes.map((n) => n.id),
                shownEdges.map((e) => e.id),
              ])}
              onlyRenderVisibleElements={shownNodes.length > 150}
              nodes={flowNodes}
              onNodesChange={onNodesChange}
              edges={visibleEdges}
              nodeTypes={nodeTypes}
              edgeTypes={edgeTypes}
              fitView={shownNodes.length > 150}
              fitViewOptions={initialFitOptions}
              defaultViewport={{ x: 24, y: 24, zoom: 0.9 }}
              onInit={setInstance}
              minZoom={0.001}
              nodesDraggable={false}
              nodesConnectable={false}
              onNodeClick={(_, n) => {
                setSelected(n.id);
                setSelectedEdge("");
              }}
              onNodeDoubleClick={(_, n) => {
                const v = n.data.node as VisualNode | undefined;
                if (v) onCenter?.(v.address);
              }}
              onEdgeMouseEnter={(_, e) => setHover(e.id)}
              onEdgeMouseLeave={() => setHover("")}
              onEdgeClick={(_, e) => {
                setSelectedEdge(e.id);
                setSelected("");
              }}
            >
              <Background />
              <Controls showInteractive={false} />
              <MiniMap
                pannable
                zoomable
                nodeColor={(n) =>
                  nodeColors[
                    (n.data.node as VisualNode | undefined)?.type ?? ""
                  ] ?? "#e2e8f0"
                }
              />
            </ReactFlow>
          </div>
          {(detail || edgeDetail) && (
            <aside
              aria-label="그래프 상세"
              className="absolute right-3 top-3 z-10 max-h-[490px] w-72 max-w-[85%] overflow-auto rounded-xl border bg-white p-4 text-sm shadow-lg"
            >
              <button
                className="mb-3 float-right text-brand"
                aria-label="그래프 상세 닫기"
                onClick={() => {
                  setSelected("");
                  setSelectedEdge("");
                }}
              >
                닫기
              </button>
              {detail ? (
                <>
                  <strong>{detail.type}</strong>
                  <p className="my-2 break-all font-mono">{detail.address}</p>
                  <p>
                    {detail.file_path}
                    {detail.line_start != null ? `:${detail.line_start}` : ""}
                  </p>
                  <CategoryBadges metadata={detail.metadata} />
                  {Array.isArray(detail.metadata?.flow_nodes) && (
                    <details open className="my-3">
                      <summary>
                        흐름 속성 ({detail.metadata.flow_nodes.length})
                      </summary>
                      <ul className="mt-2 space-y-2 text-xs">
                        {detail.metadata.flow_nodes.map((item, i) => (
                          <li className="break-all" key={i}>
                            {String((item as { address?: unknown }).address)}
                          </li>
                        ))}
                      </ul>
                    </details>
                  )}
                  <details className="my-3">
                    <summary>메타데이터</summary>
                    <pre className="mt-3 overflow-auto whitespace-pre-wrap break-all text-xs">
                      {JSON.stringify(detail.metadata, null, 2)}
                    </pre>
                  </details>
                  {onCenter && (
                    <button
                      className="mt-3 text-brand"
                      onClick={() => onCenter(detail.address)}
                    >
                      {centerLabel}
                    </button>
                  )}
                </>
              ) : edgeDetail ? (
                <>
                  <strong>{edgeDetail.type}</strong>
                  <p className="my-2 break-all">
                    {String(
                      edgeDetail.metadata?.source_address ||
                        nodeById.get(edgeDetail.source)?.address ||
                        edgeDetail.source,
                    )}{" "}
                    →{" "}
                    {String(
                      edgeDetail.metadata?.target_address ||
                        nodeById.get(edgeDetail.target!)?.address ||
                        edgeDetail.target,
                    )}
                  </p>
                  <p className="break-all">{edgeDetail.expression}</p>
                </>
              ) : (
                <p>노드 또는 연결선을 선택하면 상세 정보를 표시합니다.</p>
              )}
            </aside>
          )}
        </div>
      )}
      <details
        className="mt-3 rounded border p-3"
        onToggle={(e) => setEdgeListOpen(e.currentTarget.open)}
      >
        <summary>연결선 목록 ({shownEdges.length}) · 겹친 연결 확인</summary>
        <div className="max-h-64 overflow-auto">
          {edgeListOpen &&
            shownEdges.slice(0, edgeListLimit).map((e) => (
              <button
                key={e.id}
                className="block w-full break-all border-b py-2 text-left text-xs"
                onClick={() => {
                  setSelectedEdge(e.id);
                  setSelected("");
                }}
              >
                <strong>{e.type}</strong> ·{" "}
                {String(
                  e.metadata?.source_address || nodeById.get(e.source)?.address,
                )}{" "}
                →{" "}
                {String(
                  e.metadata?.target_address ||
                    nodeById.get(e.target!)?.address,
                )}
              </button>
            ))}
          {edgeListOpen && shownEdges.length > edgeListLimit && (
            <button
              className="p-2 text-brand"
              onClick={() => setEdgeListLimit((n) => n + 100)}
            >
              연결선 100개 더 보기
            </button>
          )}
        </div>
      </details>
    </section>
  );
}
