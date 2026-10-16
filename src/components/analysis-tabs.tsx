"use client";
import { DependencyGraph } from "./graph/dependency-graph";
import { FlowGraph, TraceGraph, AttackGraph } from "./graph/flow-graph";
import { useState } from "react";
import { api, unwrap } from "@/api/client";
import type { components } from "@/api/generated/schema";
import { useApi } from "@/api/hooks";
import { Panel, State, Badge, Pager, FindingView } from "./live-shared";
type Severity = components["schemas"]["Severity"];
const path = (id: string) => ({ scan_id: id });
export function FindingsTab({
  id,
  scanner,
  examples = 0,
}: {
  id: string;
  scanner: "checkov" | "source_trust" | "correlation";
  /** 이 도구가 예제·테스트에서 찾은 건수. 0이면 구분 버튼을 숨긴다. */
  examples?: number;
}) {
  const [offset, setOffset] = useState(0);
  const [scope, setScope] = useState<"MODULE" | "EXAMPLE">("MODULE");
  const [severity, setSeverity] = useState<Severity>();
  const [ruleId, setRuleId] = useState("");
  const [resourceType, setResourceType] = useState("");
  const [selected, setSelected] = useState("");
  const r = useApi(
    (s) =>
      scanner === "correlation"
        ? unwrap(
            api.GET("/api/scans/{scan_id}/correlations", {
              signal: s,
              params: {
                path: path(id),
                query: { severity, limit: 20, offset },
              },
            }),
          )
        : unwrap(
            api.GET("/api/scans/{scan_id}/findings", {
              signal: s,
              params: {
                path: path(id),
                query: {
                  scanner,
                  severity,
                  scope,
                  rule_id: ruleId || undefined,
                  resource_type: resourceType || undefined,
                  limit: 20,
                  offset,
                },
              },
            }),
          ),
    `${id}:${scanner}:${severity}:${scope}:${offset}:${ruleId}:${resourceType}`,
  );
  return (
    <>
      {examples > 0 && scanner !== "correlation" && (
        <div className="mt-4 flex gap-2" role="group" aria-label="탐지 위치">
          {(
            [
              ["MODULE", "모듈 본체"],
              ["EXAMPLE", `예제에서 ${examples}건`],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              aria-pressed={scope === value}
              className={`rounded-full border px-3 py-1 text-xs ${scope === value ? "border-brand bg-[#eeebff] font-semibold text-brand" : "border-line bg-white text-slate-500"}`}
              onClick={() => {
                setScope(value);
                setOffset(0);
              }}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {scanner !== "correlation" && (
        <div className="mt-4 flex gap-3">
          <input
            className="input"
            aria-label="Rule ID 필터"
            placeholder="Rule ID"
            value={ruleId}
            onChange={(e) => {
              setRuleId(e.target.value);
              setOffset(0);
            }}
          />
          <input
            className="input"
            aria-label="Resource type 필터"
            placeholder="Resource type"
            value={resourceType}
            onChange={(e) => {
              setResourceType(e.target.value);
              setOffset(0);
            }}
          />
        </div>
      )}
      <select
        className="input my-4"
        aria-label="Severity"
        value={severity || ""}
        onChange={(e) => {
          setSeverity((e.target.value as Severity) || undefined);
          setOffset(0);
        }}
      >
        {["", "CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO", "UNKNOWN"].map(
          (x) => (
            <option key={x} value={x}>
              {x || "전체 위험도"}
            </option>
          ),
        )}
      </select>
      {!r.data ? (
        <State {...r} />
      ) : (
        <>
          {r.data.map((f) => (
            <FindingView
              key={f.id}
              finding={f}
              onOpen={
                scanner === "correlation" ? () => setSelected(f.id) : undefined
              }
            />
          ))}
          {!r.data.length && (
            <Panel>
              <h2>
                {scanner === "correlation"
                  ? "연결된 고위험 경로가 발견되지 않았습니다."
                  : "탐지된 항목이 없습니다."}
              </h2>
              {scanner === "correlation" && (
                <p className="mt-3 text-slate-500">
                  개별 Checkov 또는 Source Trust 탐지가 존재하더라도 실제 데이터
                  흐름이나 dependency 근거가 없으면 상관 위험으로 생성하지
                  않습니다.
                </p>
              )}
            </Panel>
          )}
          <Pager offset={offset} count={r.data.length} setOffset={setOffset} />
        </>
      )}
      {selected && (
        <CorrelationDetail
          id={id}
          findingId={selected}
          close={() => setSelected("")}
        />
      )}
    </>
  );
}
function CorrelationDetail({
  id,
  findingId,
  close,
}: {
  id: string;
  findingId: string;
  close: () => void;
}) {
  const r = useApi(
    (s) =>
      unwrap(
        api.GET("/api/scans/{scan_id}/correlations/{finding_id}", {
          signal: s,
          params: { path: { scan_id: id, finding_id: findingId } },
        }),
      ),
    findingId,
  );
  return (
    <section
      role="region"
      aria-label="상관 분석 근거"
      className="my-6 rounded-xl border-2 border-violet-200 bg-violet-50 p-5"
    >
      <button className="float-right text-brand" onClick={close}>
        닫기
      </button>
      <h2>상관 분석 근거 · 영향 경로</h2>
      {!r.data ? (
        <State {...r} />
      ) : (
        <>
          <FindingView finding={r.data.finding} />
          <AttackGraph
            steps={r.data.attack_path}
            rule={r.data.finding.rule_id}
            metadata={{ ...r.data.finding.raw_data, ...r.data.evidence }}
          />
          <details className="mt-4">
            <summary>Evidence ID / 경로 메타데이터</summary>
            <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all text-xs">
              {JSON.stringify(r.data.evidence, null, 2)}
            </pre>
          </details>
        </>
      )}
    </section>
  );
}
export function DependenciesTab({ id }: { id: string }) {
  const r = useApi(
    (s) =>
      unwrap(
        api.GET("/api/scans/{scan_id}/dependencies", {
          signal: s,
          params: { path: path(id) },
        }),
      ),
    id,
  );
  return (
    <>
      {!r.data ? (
        <State {...r} />
      ) : (
        <>
          <Panel title="Modules">
            {r.data.modules.map((m) => (
              <article
                key={m.address}
                className="my-3 border-b border-line pb-3"
              >
                <code>{m.address}</code>
                <p className="my-2 break-all">{m.source || "-"}</p>
                <Badge value={m.source_type} />{" "}
                <span>
                  {m.source_type === "LOCAL"
                    ? "로컬 모듈"
                    : `${m.version || m.ref || "-"} · ${m.version_type} / ${m.ref_type}`}
                </span>
              </article>
            ))}
            {!r.data.modules.length && <p>Child module이 없습니다.</p>}
          </Panel>
          <Panel title="Providers">
            {r.data.providers.map((p) => (
              <p key={p.address} className="my-3 break-all">
                {p.address} · {p.source || "-"} · {p.version || "-"} ·{" "}
                {p.version_type}{" "}
                <Badge value={p.official ? "official" : "community"} />{" "}
                <Badge
                  value={
                    p.version_pinned ? "version_pinned" : "version_unpinned"
                  }
                />
              </p>
            ))}
            {!r.data.providers.length && <p>Provider 선언이 없습니다.</p>}
          </Panel>
          <DependencyGraph id={id} />
          <Entities id={id} />
        </>
      )}
    </>
  );
}
function Entities({ id }: { id: string }) {
  const [offset, setOffset] = useState(0);
  const r = useApi(
    (s) =>
      unwrap(
        api.GET("/api/scans/{scan_id}/entities", {
          signal: s,
          params: { path: path(id), query: { offset, limit: 20 } },
        }),
      ),
    `${id}:${offset}`,
  );
  return (
    <Panel title="Parsed Entities">
      {!r.data ? (
        <State {...r} />
      ) : (
        <>
          {r.data.map((e) => (
            <p key={e.id} className="my-2 break-all">
              <Badge value={e.entity_type} /> <code>{e.address}</code> ·{" "}
              {e.file_path}:{e.line_start ?? "-"}
            </p>
          ))}
          <Pager offset={offset} count={r.data.length} setOffset={setOffset} />
        </>
      )}
    </Panel>
  );
}
export function TraceForm({ id }: { id: string }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [depth, setDepth] = useState(10);
  const [result, setResult] =
    useState<components["schemas"]["FlowTraceResponse"]>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Panel title="Flow Trace">
      <form
        className="space-y-3"
        onSubmit={async (e) => {
          e.preventDefault();
          setError("");
          setResult(undefined);
          setBusy(true);
          try {
            setResult(
              await unwrap(
                api.GET("/api/scans/{scan_id}/flow-trace", {
                  params: {
                    path: path(id),
                    query: { from, to, max_depth: depth },
                  },
                }),
              ),
            );
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label className="label">
          Source address
          <input
            className="input mt-2"
            required
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            placeholder="module.policy_source.policy"
          />
        </label>
        <label className="label">
          Target address
          <input
            className="input mt-2"
            required
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="aws_iam_role_policy.admin.policy"
          />
        </label>
        <label className="label">
          최대 깊이
          <input
            className="input mt-2"
            type="number"
            min="1"
            max="30"
            value={depth}
            onChange={(e) => setDepth(Number(e.target.value))}
          />
        </label>
        <button
          className="rounded-lg bg-brand px-4 py-2 text-white"
          disabled={busy}
        >
          {busy ? "조회 중…" : "흐름 추적"}
        </button>
      </form>
      {error && (
        <p role="alert" className="mt-3 text-red-700">
          {error}
        </p>
      )}
      {result && (
        <div className="mt-4">
          {!result.found && (
            <p>지정한 탐색 범위에서 연결된 흐름을 찾지 못했습니다.</p>
          )}
          {result.truncated && (
            <p>탐색 한도에 도달했습니다. 결과는 일부 경로입니다.</p>
          )}
          <TraceGraph key={JSON.stringify(result)} result={result} />
        </div>
      )}
    </Panel>
  );
}
export function FlowsTab({ id }: { id: string }) {
  const [list, setList] = useState(false);
  const [source, setSource] = useState("");
  const [target, setTarget] = useState("");
  const [type, setType] = useState("");
  const [offset, setOffset] = useState(0);
  const [entity, setEntity] = useState("");
  const r = useApi(
    (s) =>
      unwrap(
        api.GET("/api/scans/{scan_id}/flows", {
          signal: s,
          params: {
            path: path(id),
            query: {
              source: source || undefined,
              target: target || undefined,
              edge_type: type || undefined,
              limit: 20,
              offset,
            },
          },
        }),
      ),
    `${id}:${source}:${target}:${type}:${offset}`,
  );
  return (
    <>
      <Panel title="Data Flow">
        <div className="grid gap-3 md:grid-cols-3">
          <input
            aria-label="Flow source"
            placeholder="Source 검색"
            className="input"
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              setOffset(0);
            }}
          />
          <input
            aria-label="Flow target"
            placeholder="Target 검색"
            className="input"
            value={target}
            onChange={(e) => {
              setTarget(e.target.value);
              setOffset(0);
            }}
          />
          <select
            aria-label="Flow type"
            className="input"
            value={type}
            onChange={(e) => {
              setType(e.target.value);
              setOffset(0);
            }}
          >
            {[
              "",
              "PASSES_TO_MODULE_INPUT",
              "RETURNS_FROM_MODULE_OUTPUT",
              "PROPAGATES_THROUGH_LOCAL",
              "SINKS_TO_RESOURCE_ATTRIBUTE",
              "PASSES_TO_OUTPUT",
              "FLOWS_TO",
            ].map((t) => (
              <option key={t} value={t}>
                {t || "전체 edge type"}
              </option>
            ))}
          </select>
        </div>
        {!r.data ? (
          <State {...r} />
        ) : (
          <>
            <p className="my-4 text-xs text-slate-500">
              일치하는 흐름 {r.data.summary.total_flows ?? 0}개
            </p>
            <label>
              <input
                type="checkbox"
                checked={list}
                onChange={(e) => setList(e.target.checked)}
              />
              목록 보기
            </label>
            {!list && (
              <FlowGraph
                nodes={r.data.nodes}
                edges={r.data.edges}
                onCenter={setEntity}
              />
            )}
            {list &&
              r.data.edges.map((e) => (
                <article
                  key={e.id}
                  className="my-3 rounded border border-line p-3"
                >
                  <button
                    className="break-all text-left font-mono text-brand"
                    onClick={() => setEntity(e.source)}
                  >
                    {e.source}
                  </button>
                  <p className="my-2 text-xs text-slate-400">↓ {e.type}</p>
                  <button
                    className="break-all text-left font-mono text-brand"
                    onClick={() => setEntity(e.target)}
                  >
                    {e.target}
                  </button>
                  <p className="mt-3 text-xs">
                    {[
                      "source_category",
                      "sink_category",
                      "resolved",
                      "opaque",
                      "external",
                    ]
                      .map((k) => `${k}: ${e.metadata?.[k] ?? "-"}`)
                      .join(" · ")}
                  </p>
                </article>
              ))}
            {!r.data.edges.length && <p>일치하는 흐름이 없습니다.</p>}
            <Pager
              offset={offset}
              count={r.data.edges.length}
              setOffset={setOffset}
            />
          </>
        )}
      </Panel>
      {entity && <EntityFlow id={id} address={entity} />}
      <TraceForm id={id} />
    </>
  );
}
function EntityFlow({ id, address }: { id: string; address: string }) {
  const [list, setList] = useState(false);
  const r = useApi(
    (s) =>
      unwrap(
        api.GET("/api/scans/{scan_id}/flows/{entity_address}", {
          signal: s,
          params: { path: { scan_id: id, entity_address: address } },
        }),
      ),
    `${id}:${address}`,
  );
  return (
    <Panel title={`인접 흐름: ${address}`}>
      {!r.data ? (
        <State {...r} />
      ) : (
        <>
          <label>
            <input
              type="checkbox"
              checked={list}
              onChange={(e) => setList(e.target.checked)}
            />
            목록 보기
          </label>
          {!list && (
            <FlowGraph
              nodes={r.data.nodes ?? []}
              edges={[
                ...new Map(
                  [...r.data.upstream, ...r.data.downstream].map((e) => [
                    e.id,
                    e,
                  ]),
                ).values(),
              ]}
              label="인접 흐름 그래프"
            />
          )}
          {list &&
            (["upstream", "downstream"] as const).map((k) => (
              <div key={k}>
                <h3 className="my-3">{k}</h3>
                {r.data![k].map((e) => (
                  <p key={e.id} className="my-2 break-all font-mono text-xs">
                    {e.source} → {e.target}
                  </p>
                ))}
                {!r.data![k].length && <p>없음</p>}
              </div>
            ))}
          {r.data.truncated && <p>일부 흐름만 표시됩니다.</p>}
        </>
      )}
    </Panel>
  );
}
