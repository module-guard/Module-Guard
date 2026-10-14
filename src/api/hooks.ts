"use client";
import { useEffect, useState } from "react";
export function useApi<T>(
  load: (signal: AbortSignal) => Promise<T>,
  key: string,
  poll?: (value: T) => boolean,
) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setData(undefined);
    setError("");
    async function run() {
      try {
        const value = await load(controller.signal);
        if (!controller.signal.aborted) {
          setData(value);
          setError("");
          if (poll?.(value)) timer = setTimeout(run, 1500);
        }
      } catch (e) {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "조회 실패");
      }
    }
    void run();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
    // Callers encode every request parameter in key; avoids request loops from closures.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, revision]);
  return { data, error, retry: () => setRevision((x) => x + 1) };
}
