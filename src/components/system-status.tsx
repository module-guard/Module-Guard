"use client";
import { api, unwrap } from "@/api/client";
import { useApi } from "@/api/hooks";
import { Panel, State, Badge } from "./live-shared";
export function SystemStatus() {
  const r = useApi(
    (s) => unwrap(api.GET("/api/health", { signal: s })),
    "health",
  );
  return (
    <Panel title="시스템 상태">
      <button
        onClick={r.retry}
        className="mb-4 rounded border border-line px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
      >
        다시 확인
      </button>
      {!r.data ? (
        <State {...r} />
      ) : (
        <div className="space-y-3">
          <p>
            API <Badge value="Connected" />
          </p>
          <p>
            DB <Badge value={r.data.database} />
          </p>
          <p>
            Checkov <Badge value={r.data.checkov} />
          </p>
          <p>
            분석 엔진 <Badge value={r.data.static_analysis || "-"} />
          </p>
          <p>
            전체 <Badge value={r.data.status} />
          </p>
        </div>
      )}
    </Panel>
  );
}
