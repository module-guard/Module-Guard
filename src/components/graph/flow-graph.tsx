"use client";
import { useState } from "react";
import type { components } from "@/api/generated/schema";
import type { FlowNode, FlowEdge, VisualNode, VisualEdge } from "./types";
import { GraphCanvas } from "./graph-canvas";
export function flowGraph(
  nodes: FlowNode[],
  edges: FlowEdge[],
): { nodes: VisualNode[]; edges: VisualEdge[] } {
  return {
    nodes: [...new Map(nodes.map((n) => [n.address, n])).values()].map((n) => ({
      id: n.address,
      address: n.address,
      type: n.entity_type ?? "UNRESOLVED",
      module_path: Array.isArray(n.metadata?.module_path)
        ? (n.metadata.module_path as string[])
        : [],
      file_path:
        typeof n.metadata?.file_path === "string"
          ? n.metadata.file_path
          : undefined,
      line_start:
        typeof n.metadata?.line_start === "number"
          ? n.metadata.line_start
          : undefined,
      metadata: {
        ...n.metadata,
        ...Object.fromEntries(
          [
            "entity_address",
            "attribute_path",
            "source_category",
            "sink_category",
            "opaque",
            "external",
            "resolved",
            "sensitive_input",
          ]
            .filter((k) => n[k as keyof FlowNode] !== undefined)
            .map((k) => [k, n[k as keyof FlowNode]]),
        ),
      },
    })),
    edges,
  };
}
/** Group attribute nodes by their declared entity without dropping any edge IDs. */
export function entityFlowGraph(nodes: FlowNode[], edges: FlowEdge[]) {
  const raw = flowGraph(nodes, edges);
  const owner = new Map(
    raw.nodes.map((n) => [n.id, String(n.metadata?.entity_address || n.id)]),
  );
  const groups = new Map<string, VisualNode[]>();
  for (const n of raw.nodes) {
    const id = owner.get(n.id)!;
    groups.set(id, [...(groups.get(id) ?? []), n]);
  }
  return {
    nodes: [...groups].map(([id, members]) => ({
      ...members[0],
      id,
      address: id,
      metadata: {
        ...members[0].metadata,
        attribute_path: undefined,
        source_category: undefined,
        sink_category: undefined,
        source_categories: [
          ...new Set(
            members.map((n) => n.metadata?.source_category).filter(Boolean),
          ),
        ],
        sink_categories: [
          ...new Set(
            members.map((n) => n.metadata?.sink_category).filter(Boolean),
          ),
        ],
        attribute_count: members.length,
        flow_nodes: members.map((n) => ({ address: n.address, ...n.metadata })),
        opaque: members.some((n) => n.metadata?.opaque === true),
        external: members.some((n) => n.metadata?.external === true),
        resolved: members.every((n) => n.metadata?.resolved !== false),
      },
    })),
    edges: raw.edges.map((e) => ({
      ...e,
      source: owner.get(e.source) ?? e.source,
      target: e.target ? (owner.get(e.target) ?? e.target) : null,
      metadata: {
        ...e.metadata,
        source_address: e.source,
        target_address: e.target,
      },
    })),
  };
}
export function FlowGraph({
  nodes,
  edges,
  label = "데이터 흐름 그래프",
  onCenter,
}: {
  nodes: FlowNode[];
  edges: FlowEdge[];
  label?: string;
  onCenter?: (address: string) => void;
}) {
  const [grouped, setGrouped] = useState(label !== "흐름 추적 그래프");
  return (
    <>
      <label className="my-3 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={grouped}
          onChange={(e) => setGrouped(e.target.checked)}
        />
        선언별 묶기 (속성 연결 유지)
      </label>
      <p className="text-xs text-slate-500">
        원본 흐름 노드 {nodes.length}개 · 연결 {edges.length}개 ·{" "}
        {grouped
          ? "묶인 속성은 노드 상세에서 확인할 수 있습니다."
          : "속성별 노드 표시"}
      </p>
      <GraphCanvas
        key={String(grouped)}
        {...(grouped ? entityFlowGraph(nodes, edges) : flowGraph(nodes, edges))}
        label={label}
        onCenter={onCenter}
        centerLabel="인접 흐름 보기"
      />
    </>
  );
}
export function TraceGraph({
  result,
}: {
  result: components["schemas"]["FlowTraceResponse"];
}) {
  const [index, setIndex] = useState(0);
  const [list, setList] = useState(false);
  const current = result.paths[index] ?? [];
  const edges =
    result.path_edges?.[index] ??
    current.slice(1).map((n, i) => ({
      id: result.path_edge_ids[index]?.[i] ?? `trace:${i}`,
      source: current[i].address,
      target: n.address,
      type: "FLOW",
      expression: null,
      metadata: {},
    }));
  return (
    <>
      <div role="tablist" aria-label="추적 경로" className="my-3 flex gap-3">
        {result.paths.map((_, i) => (
          <button
            role="tab"
            aria-selected={i === index}
            key={i}
            onClick={() => setIndex(i)}
          >
            경로 {i + 1}
          </button>
        ))}
      </div>
      <label>
        <input
          type="checkbox"
          checked={list}
          onChange={(e) => setList(e.target.checked)}
        />
        목록 보기
      </label>
      {list ? (
        <ol>
          {current.map((n, i) => (
            <li key={i} className="my-2 break-all font-mono">
              {n.address}
            </li>
          ))}
        </ol>
      ) : (
        <FlowGraph nodes={current} edges={edges} label="흐름 추적 그래프" />
      )}
    </>
  );
}
export function AttackGraph({
  steps,
  rule,
  metadata,
}: {
  steps: Record<string, unknown>[];
  rule: string;
  metadata?: Record<string, unknown>;
}) {
  const nodes: VisualNode[] = [
    ...steps.map((step, i) => ({
      id: `attack:${i}`,
      address: String(step.address || step.rule || step.type || "FLOW"),
      type: String(step.type || "FLOW"),
      metadata: step,
    })),
    { id: "attack:result", address: rule, type: "TFCR", metadata },
  ];
  const edges = nodes.slice(1).map((n, i) => ({
    id: `attack-edge:${i}`,
    source: nodes[i].id,
    target: n.id,
    type: "EVIDENCE",
  }));
  return (
    <>
      <div className="my-3 flex gap-2">
        {["confidence", "path_quality"].map((k) => (
          <span key={k} className="rounded bg-violet-100 px-2 py-1 text-xs">
            {k}: {String(metadata?.[k] ?? "UNKNOWN")}
          </span>
        ))}
      </div>
      <GraphCanvas nodes={nodes} edges={edges} label="상관 공격 경로 그래프" />
    </>
  );
}
