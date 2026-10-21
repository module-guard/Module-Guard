import { layoutGraph } from "./layout";
import type { VisualEdge, VisualNode } from "./types";

self.onmessage = (
  event: MessageEvent<{ nodes: VisualNode[]; edges: VisualEdge[] }>,
) => {
  try {
    self.postMessage({
      nodes: layoutGraph(event.data.nodes, event.data.edges),
    });
  } catch {
    self.postMessage({ error: true });
  }
};
