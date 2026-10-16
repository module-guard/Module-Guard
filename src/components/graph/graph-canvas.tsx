"use client";
import { useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  MarkerType,
  type NodeProps,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { layoutGraph } from "./layout";
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
};
const dashed = (m?: Record<string, unknown>) =>
  m?.opaque === true || m?.external === true || m?.resolved === false;
export function CategoryBadges({
  metadata: m,
}: {
  metadata?: Record<string, unknown>;
}) {
  return (
    <span className="flex flex-wrap gap-1">
      {["source_category", "sink_category"].map((k) =>
        m?.[k] ? (
          <span
            key={k}
            className={`rounded px-1 text-xs ${k === "sink_category" && m[k] !== "UNKNOWN" ? "bg-red-100 font-bold text-red-800" : "bg-blue-100 text-blue-800"}`}
          >
            {String(m[k])}
          </span>
        ) : null,
      )}
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
      <div className="truncate text-base font-semibold">
        {n.address.split(".").slice(-2).join(".")}
      </div>
      <CategoryBadges metadata={n.metadata} />
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
export function GraphCanvas({
  nodes = [],
  edges = [],
  loading,
  error,
  retry,
  onCenter,
  label = "의존성 그래프",
}: {
  nodes?: VisualNode[];
  edges?: VisualEdge[];
  loading?: boolean;
  error?: string;
  retry?: () => void;
  onCenter?: (address: string) => void;
  label?: string;
}) {
  const [selected, setSelected] = useState("");
  const [hover, setHover] = useState("");
  const [selectedEdge, setSelectedEdge] = useState("");
  const positioned = useMemo(
    () => (nodes.length <= 300 ? layoutGraph(nodes, edges) : []),
    [nodes, edges],
  );
  const visibleEdges = useMemo<Edge[]>(
    () =>
      edges
        .filter(
          (e) =>
            e.target &&
            nodes.some((n) => n.id === e.source) &&
            nodes.some((n) => n.id === e.target),
        )
        .map((e) => ({
          id: e.id,
          source: e.source,
          target: e.target!,
          type: "smoothstep",
          label: hover === e.id || selectedEdge === e.id ? e.type : undefined,
          style: edgeStyle(e),
          markerEnd: { type: MarkerType.ArrowClosed },
          selected: selectedEdge === e.id,
        })),
    [edges, nodes, hover, selectedEdge],
  );
  const detail = nodes.find((n) => n.id === selected);
  const edgeDetail = edges.find((e) => e.id === selectedEdge);
  if (error)
    return (
      <div role="alert">
        {error}
        {retry && <button onClick={retry}>다시 시도</button>}
      </div>
    );
  if (loading) return <p role="status">그래프를 불러오는 중…</p>;
  if (nodes.length > 300)
    return <p role="status">노드가 300개를 넘습니다. 범위를 좁혀 주세요.</p>;
  if (!nodes.length) return <p role="status">표시할 그래프 노드가 없습니다.</p>;
  return (
    <>
      <div className="my-3 flex flex-wrap gap-3 text-xs" aria-label="노드 범례">
        {Object.entries(nodeColors).map(([type, color]) => (
          <span key={type} style={{ color }}>
            {type === "UNRESOLVED" ? "┄" : "●"} {type}
          </span>
        ))}
      </div>
      <div className="flex flex-col gap-3 lg:flex-row">
        <div
          role="img"
          aria-label={label}
          className="h-[540px] min-w-0 flex-1 rounded border border-line"
        >
          <ReactFlow
            key={nodes.map((n) => n.id).join("|")}
            nodes={positioned.map((n) => ({
              ...n,
              selected: n.id === selected,
            }))}
            edges={visibleEdges}
            nodeTypes={nodeTypes}
            fitView
            minZoom={0.1}
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
        <aside
          aria-label="그래프 상세"
          className="w-full rounded border border-line p-3 text-sm lg:w-64"
        >
          {detail ? (
            <>
              <strong>{detail.type}</strong>
              <p className="my-2 break-all font-mono">{detail.address}</p>
              <p>
                {detail.file_path}
                {detail.line_start != null ? `:${detail.line_start}` : ""}
              </p>
              <CategoryBadges metadata={detail.metadata} />
              <pre className="mt-3 overflow-auto whitespace-pre-wrap break-all text-xs">
                {JSON.stringify(detail.metadata, null, 2)}
              </pre>
              {onCenter && (
                <button
                  className="mt-3 text-brand"
                  onClick={() => onCenter(detail.address)}
                >
                  이 노드 중심으로 보기
                </button>
              )}
            </>
          ) : edgeDetail ? (
            <>
              <strong>{edgeDetail.type}</strong>
              <p className="break-all">{edgeDetail.expression}</p>
            </>
          ) : (
            <p>노드 또는 연결선을 선택하면 상세 정보를 표시합니다.</p>
          )}
        </aside>
      </div>
    </>
  );
}
