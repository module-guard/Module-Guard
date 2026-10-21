"use client";
import { useEffect, useMemo, useState } from "react";
import type { Node } from "@xyflow/react";
import { layoutGraph } from "./layout";
import type { VisualEdge, VisualNode } from "./types";

/** Large layouts never block input. Replaced requests terminate their worker. */
export function useGraphLayout(nodes: VisualNode[], edges: VisualEdge[]) {
  const large = nodes.length > 150;
  const small = useMemo(
    () => (large ? [] : layoutGraph(nodes, edges)),
    [large, nodes, edges],
  );
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{
    input: VisualNode[];
    edges: VisualEdge[];
    nodes: Node[];
    error: boolean;
  }>();
  useEffect(() => {
    if (!large) return;
    let worker: Worker | undefined;
    const fail = () =>
      setResult({ input: nodes, edges, nodes: [], error: true });
    try {
      worker = new Worker(new URL("./layout.worker.ts", import.meta.url));
      worker.onmessage = (
        event: MessageEvent<{ nodes?: Node[]; error?: boolean }>,
      ) => {
        setResult({
          input: nodes,
          edges,
          nodes: event.data.nodes ?? [],
          error: !!event.data.error,
        });
        worker?.terminate();
      };
      worker.onerror = fail;
      worker.postMessage({ nodes, edges });
    } catch {
      fail();
    }
    return () => worker?.terminate();
  }, [large, nodes, edges, attempt]);
  const current =
    result?.input === nodes && result.edges === edges ? result : undefined;
  return {
    positioned: large ? (current?.nodes ?? small) : small,
    pending: large && !current,
    layoutError: large && !!current?.error,
    retryLayout: () => {
      setResult(undefined);
      setAttempt((n) => n + 1);
    },
  };
}
