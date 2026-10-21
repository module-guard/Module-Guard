import dagre from "@dagrejs/dagre";
import { Position, type Node } from "@xyflow/react";
import type { VisualNode, VisualEdge } from "./types";
export const WIDTH = 300,
  HEIGHT = 160;
export function layoutGraph(nodes: VisualNode[], edges: VisualEdge[]): Node[] {
  const groups = new Map<string, VisualNode[]>();
  for (const n of nodes) {
    const key = JSON.stringify(n.module_path ?? []);
    const members = groups.get(key) ?? [];
    members.push(n);
    groups.set(key, members);
  }
  const outer = new dagre.graphlib.Graph()
    .setGraph({ rankdir: "LR", nodesep: 60, ranksep: 100 })
    .setDefaultEdgeLabel(() => ({}));
  const owner = new Map<string, string>();
  const local = new Map<
    string,
    {
      graph: InstanceType<typeof dagre.graphlib.Graph>;
      width: number;
      height: number;
    }
  >();
  for (const [key, members] of groups) {
    for (const n of members) owner.set(n.id, key);
  }
  const internalEdges = new Map<string, VisualEdge[]>();
  for (const e of edges) {
    const key = owner.get(e.source);
    if (key && e.target && key === owner.get(e.target)) {
      const list = internalEdges.get(key) ?? [];
      list.push(e);
      internalEdges.set(key, list);
    }
  }
  for (const [key, members] of groups) {
    const g = new dagre.graphlib.Graph()
      .setGraph({
        rankdir: "LR",
        ranker: nodes.length > 150 ? "longest-path" : "network-simplex",
        nodesep: 40,
        ranksep: 100,
        marginx: 24,
        marginy: 48,
      })
      .setDefaultEdgeLabel(() => ({}));
    const ids = new Set(members.map((n) => n.id));
    members.forEach((n) => {
      g.setNode(n.id, { width: WIDTH, height: HEIGHT });
      owner.set(n.id, key);
    });
    (internalEdges.get(key) ?? []).forEach((e) => {
      if (ids.has(e.source) && e.target && ids.has(e.target))
        g.setEdge(e.source, e.target);
    });
    dagre.layout(g);
    const { width = WIDTH, height = HEIGHT } = g.graph();
    local.set(key, { graph: g, width, height });
    outer.setNode(key, { width, height });
  }
  edges.forEach((e) => {
    const a = owner.get(e.source),
      b = e.target && owner.get(e.target);
    if (a && b && a !== b) outer.setEdge(a, b);
  });
  dagre.layout(outer);
  const result: Node[] = [];
  for (const [key, members] of groups) {
    const { graph, width, height } = local.get(key)!;
    const p = outer.node(key);
    const parentId = `group:${key}`;
    result.push({
      id: parentId,
      type: "moduleGroup",
      width,
      height,
      measured: { width, height },
      position: { x: p.x - width / 2, y: p.y - height / 2 },
      data: { label: JSON.parse(key).join(" / ") || "root" },
      style: {
        width,
        height,
        background: "rgba(241,245,249,.5)",
        border: "1px solid #cbd5e1",
      },
      selectable: false,
      draggable: false,
    });
    for (const n of members) {
      const p = graph.node(n.id) as { x: number; y: number };
      result.push({
        id: n.id,
        type: "entity",
        width: WIDTH,
        height: HEIGHT,
        measured: { width: WIDTH, height: HEIGHT },
        handles: [
          {
            type: "target",
            position: Position.Left,
            x: 0,
            y: HEIGHT / 2,
            width: 6,
            height: 6,
          },
          {
            type: "source",
            position: Position.Right,
            x: WIDTH,
            y: HEIGHT / 2,
            width: 6,
            height: 6,
          },
        ],
        parentId,
        extent: "parent",
        position: { x: p.x - WIDTH / 2, y: p.y - HEIGHT / 2 },
        data: { node: n },
        style: { width: WIDTH, height: HEIGHT },
        ariaLabel: n.address,
      });
    }
  }
  return result;
}
