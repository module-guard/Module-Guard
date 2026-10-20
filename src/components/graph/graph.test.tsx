import { prepareGraph } from "./normalize";
import { collectFlowPages } from "./flow-pages";
import { describe, it, expect } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { neighborhood } from "./neighborhood";
import { layoutGraph, WIDTH, HEIGHT } from "./layout";
import { GraphCanvas, edgeStyle } from "./graph-canvas";
import { flowGraph, entityFlowGraph } from "./flow-graph";
import type { GraphNode, GraphEdge } from "./types";
const node = (id: string, module_path: string[] = []): GraphNode => ({
  id,
  address: id,
  type: "RESOURCE",
  metadata: {},
  module_path,
});
const edge = (
  source: string,
  target: string,
  type = "REFERENCES",
  id = source + target,
): GraphEdge => ({
  id,
  source,
  target,
  type,
  expression: null,
  attribute_name: null,
  target_address: target,
  metadata: {},
});
const graph = {
  nodes: [node("root"), node("a"), node("b"), node("c"), node("isolated")],
  edges: [
    edge("root", "b"),
    edge("root", "a"),
    edge("a", "c", "READS_LOCAL"),
    edge("c", "root"),
    edge("a", "a"),
    edge("root", "a", "CALLS_MODULE", "parallel"),
  ],
};
describe("neighborhood matches server BFS", () => {
  it("uses depth and preserves original edge orientation", () => {
    expect(
      neighborhood(graph, "root", 1, "out", [], 150).nodes.map((n) => n.id),
    ).toEqual(["root", "a", "b"]);
    expect(
      neighborhood(graph, "root", 2, "out", [], 150).nodes.map((n) => n.id),
    ).toEqual(["root", "a", "b", "c"]);
    expect(
      neighborhood(graph, "root", 1, "in", [], 150).edges.map((e) => e.id),
    ).toEqual(["croot"]);
    expect(neighborhood(graph, "root", 1, "both", [], 150).nodes).toHaveLength(
      4,
    );
  });
  it("filters traversal and induced edges", () => {
    const r = neighborhood(graph, "root", 3, "both", ["CALLS_MODULE"], 150);
    expect(r.nodes.map((n) => n.id)).toEqual(["root", "a"]);
    expect(r.edges.map((e) => e.id)).toEqual(["parallel"]);
  });
  it("limits deterministically, including cycles and parallel edges", () => {
    expect(neighborhood(graph, "root", 3, "out", [], 2)).toMatchObject({
      truncated: true,
      nodes: [{ id: "root" }, { id: "a" }],
    });
    expect(neighborhood(graph, "root", 1, "out", [], 3).truncated).toBe(false);
    expect(neighborhood(graph, "root", 3, "both", [], 4).truncated).toBe(false);
    expect(neighborhood(graph, "root", 1, "out", [], 1).nodes).toHaveLength(1);
  });
  it("rejects missing centers and invalid limits", () => {
    expect(() => neighborhood(graph, "missing", 1, "both", [], 10)).toThrow();
    expect(() => neighborhood(graph, "root", 1, "both", [], 0)).toThrow();
  });
});
describe("dagre layout", () => {
  it("places a chain left to right without node overlap", () => {
    const result = layoutGraph(
      [node("a"), node("b"), node("c")],
      [edge("a", "b"), edge("b", "c")],
    );
    const children = result.filter((n) => n.parentId);
    expect(children[0].position.x + WIDTH).toBeLessThan(children[1].position.x);
    expect(children[1].position.x + WIDTH).toBeLessThan(children[2].position.x);
    for (const n of children) {
      const parent = result.find((p) => p.id === n.parentId)!;
      expect(n.position.x).toBeGreaterThanOrEqual(0);
      expect(n.position.y + HEIGHT).toBeLessThanOrEqual(
        Number(parent.style!.height),
      );
    }
  });
  it("groups module paths, handles cycles and disconnected nodes deterministically", () => {
    const nodes = [
      node("a", ["a"]),
      node("b", ["b"]),
      node("c", ["a"]),
      node("d"),
    ];
    const edges = [edge("a", "b"), edge("b", "a")];
    const result = layoutGraph(nodes, edges);
    expect(result.filter((n) => !n.parentId)).toHaveLength(3);
    expect(result).toEqual(layoutGraph(nodes, edges));
    for (const n of result)
      expect(
        Number.isFinite(n.position.x) && Number.isFinite(n.position.y),
      ).toBe(true);
    expect(result.find((n) => n.id === "a")!.parentId).toEqual(
      result.find((n) => n.id === "c")!.parentId,
    );
  });
});
describe("graph states and semantic styles", () => {
  it("renders loading, empty, error and the size guard", () => {
    expect(renderToStaticMarkup(<GraphCanvas loading />)).toContain(
      "불러오는 중",
    );
    expect(renderToStaticMarkup(<GraphCanvas />)).toContain(
      "표시할 그래프 노드가 없습니다",
    );
    const error = renderToStaticMarkup(
      <GraphCanvas error="조회 실패" retry={() => {}} />,
    );
    expect(error).toContain('role="alert"');
    expect(error).toContain("다시 시도");
    expect(
      renderToStaticMarkup(
        <GraphCanvas
          nodes={Array.from({ length: 301 }, (_, i) => node(String(i)))}
        />,
      ),
    ).toContain("범위를 좁혀 주세요");
  });
  it("keeps dependency and unresolved styles distinct", () => {
    expect(edgeStyle(edge("a", "b", "CALLS_MODULE")).strokeWidth).toBe(3);
    expect(
      edgeStyle(edge("a", "b", "EXPLICIT_DEPENDS_ON")).strokeDasharray,
    ).toBeTruthy();
    expect(
      edgeStyle({ ...edge("a", "b"), metadata: { resolved: false } })
        .strokeDasharray,
    ).toBeTruthy();
  });
  it("uses flow addresses as edge identities and preserves categories", () => {
    const result = flowGraph(
      [
        {
          id: "uuid",
          address: "local.policy",
          entity_type: "LOCAL",
          opaque: true,
          external: false,
          resolved: true,
          sensitive_input: false,
          sink_category: "IAM_POLICY",
          metadata: { file_path: "main.tf", line_start: 7 },
        },
      ],
      [],
    );
    expect(result.nodes[0]).toMatchObject({
      id: "local.policy",
      file_path: "main.tf",
      line_start: 7,
      metadata: { opaque: true, sink_category: "IAM_POLICY" },
    });
  });
});

describe("graph completeness", () => {
  it("retains isolated nodes, parallel edges and absent endpoints", () => {
    const r = prepareGraph(
      [node("a"), node("isolated")],
      [
        edge("a", "b"),
        edge("a", "b", "READS_LOCAL", "parallel"),
        { ...edge("a", "x"), target: null, target_address: "module.missing" },
      ],
    );
    expect(r.nodes).toHaveLength(4);
    expect(r.edges).toHaveLength(3);
    expect(r.missing).toBe(2);
    expect(r.components.map((c) => c.length)).toEqual([3, 1]);
    expect(
      r.nodes.find((n) => n.address === "module.missing")?.metadata?.resolved,
    ).toBe(false);
    for (const e of r.edges)
      for (const id of [e.source, e.target])
        expect(r.nodes.some((n) => n.id === id)).toBe(true);
  });
  it("passes explicit dimensions to React Flow and its minimap", () => {
    for (const n of layoutGraph(graph.nodes, graph.edges)) {
      expect(n.width).toBeGreaterThan(0);
      expect(n.height).toBeGreaterThan(0);
    }
  });
  it("collects all 889 flows across the API's 50-row page limit", async () => {
    const offsets: number[] = [];
    const r = await collectFlowPages(async (offset) => {
      offsets.push(offset);
      return {
        nodes: [
          {
            id: "a",
            address: "a",
            entity_type: "LOCAL",
            metadata: {},
            external: false,
            opaque: false,
            resolved: true,
            sensitive_input: false,
          },
        ],
        edges: Array.from({ length: Math.min(50, 889 - offset) }, (_, i) => ({
          ...edge("a", "a", "FLOW", String(offset + i)),
          target: "a",
        })),
        summary: { total_flows: 889 },
        limit: 50,
        offset,
      };
    });
    expect(offsets).toHaveLength(18);
    expect(r.edges).toHaveLength(889);
    expect(r.nodes).toHaveLength(1);
    expect(r.complete).toBe(true);
  });
  it("does not claim an unexpectedly empty page is complete", async () => {
    const r = await collectFlowPages(async (offset) => ({
      nodes: [],
      edges: [],
      summary: { total_flows: 889 },
      limit: 50,
      offset,
    }));
    expect(r.complete).toBe(false);
  });
});

it("renders missing node details instead of hiding an edge-only response", () => {
  const html = renderToStaticMarkup(
    <GraphCanvas nodes={[]} edges={[edge("a", "b")]} />,
  );
  expect(html).toContain("의존성 그래프");
  expect(html).toContain("미해결 노드");
  expect(html).not.toContain("표시할 그래프 노드가 없습니다");
});

it("groups attributes without losing the individual flow edges", () => {
  const nodes = ["a.x", "a.y", "b.z"].map((address) => ({
    id: address,
    address,
    entity_address: address.split(".")[0],
    entity_type: "RESOURCE",
    metadata: { entity_address: address.split(".")[0] },
    opaque: false,
    external: false,
    resolved: true,
    sensitive_input: false,
  }));
  const edges = [
    { ...edge("a.x", "b.z", "FLOW", "x"), target: "b.z" },
    { ...edge("a.y", "b.z", "FLOW", "y"), target: "b.z" },
  ];
  const r = entityFlowGraph(nodes, edges);
  expect(r.nodes).toHaveLength(2);
  expect(r.edges.map((e) => e.id)).toEqual(["x", "y"]);
  expect(r.edges.every((e) => e.source === "a" && e.target === "b")).toBe(true);
  expect(r.nodes[0].metadata.flow_nodes).toHaveLength(2);
});
