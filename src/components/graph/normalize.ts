import type { VisualNode, VisualEdge } from "./types";
/** Preserve every response edge, explicitly representing absent endpoint details. */
export function prepareGraph(
  inputNodes: VisualNode[],
  inputEdges: VisualEdge[],
) {
  const nodes = new Map(inputNodes.map((n) => [n.id, n]));
  const edges = inputEdges.map((e) => ({
    ...e,
    target: e.target || `missing-target:${e.id}`,
  }));
  let missing = 0;
  for (const e of edges)
    for (const id of [e.source, e.target])
      if (!nodes.has(id)) {
        nodes.set(id, {
          id,
          address: id.startsWith("missing-target:")
            ? e.target_address || "대상 정보 없음"
            : id,
          type: "UNRESOLVED",
          metadata: { resolved: false, reason: "응답에 노드 상세 없음" },
        });
        missing++;
      }
  const adjacency = new Map(
    [...nodes.keys()].map((id) => [id, new Set<string>()]),
  );
  for (const e of edges) {
    adjacency.get(e.source)!.add(e.target);
    adjacency.get(e.target)!.add(e.source);
  }
  const seen = new Set<string>();
  const components: string[][] = [];
  for (const id of nodes.keys())
    if (!seen.has(id)) {
      const component = [id];
      seen.add(id);
      for (let i = 0; i < component.length; i++)
        for (const next of adjacency.get(component[i])!)
          if (!seen.has(next)) {
            seen.add(next);
            component.push(next);
          }
      components.push(component);
    }
  return { nodes: [...nodes.values()], edges, missing, components };
}
