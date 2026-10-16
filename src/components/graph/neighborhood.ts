import type { GraphNode, GraphEdge, Neighborhood, Direction } from "./types";
/** Matches the server's address-ordered BFS and induced edge selection. */
export function neighborhood(
  graph: { nodes: GraphNode[]; edges: GraphEdge[] },
  center: string,
  depth: number,
  direction: Direction,
  edgeTypes: string[],
  limit: number,
): Neighborhood {
  if (depth < 1 || depth > 3 || limit < 1 || limit > 500)
    throw new Error("Invalid graph range");
  const root = graph.nodes.find((n) => n.address === center);
  if (!root) throw new Error("Graph node address not found");
  const nodes = new Map(graph.nodes.map((n) => [n.id, n]));
  const edges = graph.edges.filter(
    (e) => !edgeTypes.length || edgeTypes.includes(e.type),
  );
  const neighbors = new Map<string, Set<string>>();
  const add = (a: string, b: string) => {
    if (!neighbors.has(a)) neighbors.set(a, new Set());
    neighbors.get(a)!.add(b);
  };
  for (const e of edges)
    if (nodes.has(e.source) && e.target && nodes.has(e.target)) {
      if (direction !== "in") add(e.source, e.target);
      if (direction !== "out") add(e.target, e.source);
    }
  const selected = new Set([root.id]);
  const queue: [string, number][] = [[root.id, 0]];
  let truncated = false;
  for (let i = 0; i < queue.length && !truncated; i++) {
    const [id, distance] = queue[i];
    if (distance === depth) continue;
    for (const next of [...(neighbors.get(id) ?? [])].sort((a, b) =>
      nodes.get(a)!.address < nodes.get(b)!.address ? -1 : 1,
    )) {
      if (selected.has(next)) continue;
      if (selected.size === limit) {
        truncated = true;
        break;
      }
      selected.add(next);
      queue.push([next, distance + 1]);
    }
  }
  return {
    schema_version: 1,
    center: root.id,
    depth,
    direction,
    truncated,
    nodes: [...selected].map((id) => nodes.get(id)!),
    edges: edges.filter(
      (e) =>
        selected.has(e.source) && e.target !== null && selected.has(e.target),
    ),
  };
}
