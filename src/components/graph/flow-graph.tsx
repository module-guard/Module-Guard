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
  return (
    <GraphCanvas
      {...flowGraph(nodes, edges)}
      label={label}
      onCenter={onCenter}
    />
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
