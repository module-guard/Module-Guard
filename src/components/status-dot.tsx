"use client";
import { useEffect, useState } from "react";
import { api, unwrap } from "@/api/client";
import type { components } from "@/api/generated/schema";
import { describe } from "@/lib/labels";
import { cn } from "@/lib/utils";
import { Tooltip } from "./ui/tooltip";
type Health = components["schemas"]["HealthResponse"];
type State = { health?: Health; down?: boolean };
export function StatusDot() {
  const [state, setState] = useState<State>({});
  useEffect(() => {
    const controller = new AbortController();
    async function check() {
      try {
        const health = await unwrap(
          api.GET("/api/health", { signal: controller.signal }),
        );
        setState({ health });
      } catch {
        if (!controller.signal.aborted) setState({ down: true });
      }
    }
    void check();
    const timer = setInterval(check, 30000);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);
  const { health, down } = state;
  const ok = health?.status === "ok";
  const text = down ? "연결 끊김" : health && !ok ? "점검 필요" : "";
  const detail = down
    ? "백엔드에 연결할 수 없습니다."
    : health
      ? [
          ["API", "Connected"],
          ["DB", health.database],
          ["Checkov", health.checkov],
          ["분석 엔진", health.static_analysis || "available"],
        ]
          .map(([name, value]) => `${name} ${describe(value).label}`)
          .join(" · ")
      : "시스템 상태 확인 중…";
  return (
    <Tooltip content={detail}>
      <button
        type="button"
        aria-label={`시스템 상태: ${text || (ok ? "정상" : "확인 중")}`}
        className="flex h-8 cursor-default items-center gap-2 rounded-full px-2 text-xs text-red-700"
      >
        <span
          className={cn(
            "h-2.5 w-2.5 rounded-full",
            down || (health && !ok)
              ? "bg-red-500"
              : ok
                ? "bg-emerald-500"
                : "bg-slate-300",
          )}
        />
        {text}
      </button>
    </Tooltip>
  );
}
