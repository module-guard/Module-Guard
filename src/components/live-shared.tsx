"use client";
import type { components } from "@/api/generated/schema";
import { describe, toneClass } from "@/lib/labels";
import { exactTime, relativeTime } from "@/lib/format";
export type Scan = components["schemas"]["ScanResponse"];
export type Finding = components["schemas"]["FindingResponse"];
export function Panel({
  children,
  title,
}: {
  children: React.ReactNode;
  title?: string;
}) {
  return (
    <section className="panel my-4 overflow-hidden p-5">
      {title && <h2 className="mb-4">{title}</h2>}
      {children}
    </section>
  );
}
export function State({
  error,
  retry,
}: {
  error?: string;
  retry?: () => void;
}) {
  return (
    <Panel>
      <p role={error ? "alert" : "status"}>{error || "불러오는 중…"}</p>
      {error && retry && (
        <button className="mt-3 text-brand" onClick={retry}>
          다시 시도
        </button>
      )}
    </Panel>
  );
}
export function Badge({ value }: { value: string }) {
  const { label, tone, hint } = describe(value);
  return (
    <span
      title={hint}
      className={`inline-block whitespace-nowrap rounded border px-2 py-0.5 text-xs ${toneClass[tone]}`}
    >
      {label}
    </span>
  );
}
export function Time({ value }: { value: string }) {
  return (
    <time dateTime={value} title={exactTime(value)}>
      {relativeTime(value)}
    </time>
  );
}
export function DemoLabel({ demo }: { demo?: boolean }) {
  return demo ? (
    <p className="my-2 rounded bg-amber-50 p-3 text-amber-800">
      개발용 Fixture 시나리오 · Checkov 입력은 고정 fixture이며 실제 GitHub 검사
      결과가 아닙니다.
    </p>
  ) : null;
}
export function shortUrl(url: string) {
  return url.replace(/^https?:\/\//, "").replace(/\.git$/, "");
}
export function Pager({
  offset,
  count,
  setOffset,
}: {
  offset: number;
  count: number;
  setOffset: (n: number) => void;
}) {
  // 한 페이지에 다 들어가면 페이지 넘김을 숨긴다.
  if (!offset && count < 20) return null;
  return (
    <div className="my-4 flex items-center gap-4">
      <button
        disabled={!offset}
        onClick={() => setOffset(Math.max(0, offset - 20))}
      >
        이전
      </button>
      <span>
        {count ? offset + 1 : 0}–{offset + count}
      </span>
      <button disabled={count < 20} onClick={() => setOffset(offset + 20)}>
        다음
      </button>
    </div>
  );
}
const severityBars = [
  ["critical", "Critical", "bg-red-500"],
  ["high", "High", "bg-orange-400"],
  ["medium", "Medium", "bg-amber-400"],
  ["low", "Low", "bg-blue-400"],
  ["info", "Info", "bg-slate-400"],
] as const;
/** 심각도별 막대. 미분류는 비율에서 빼고 아래에 따로 적는다. */
export function SeverityBars({ summary }: { summary: Scan["summary"] }) {
  const classified = summary.total - summary.unknown;
  return (
    <div>
      {severityBars.map(([key, label, color]) => (
        <div key={key} className="my-2.5 flex items-center gap-3">
          <span className="w-16 text-slate-600">{label}</span>
          <div className="h-2 flex-1 rounded bg-slate-100">
            <div
              className={`h-2 rounded ${color}`}
              style={{
                width: `${classified ? (summary[key] / classified) * 100 : 0}%`,
              }}
            />
          </div>
          <strong className="w-10 text-right tabular-nums">
            {summary[key]}
          </strong>
        </div>
      ))}
      {summary.unknown > 0 && (
        <p
          className="mt-4 text-xs text-slate-400"
          title="검사 도구가 심각도를 제공하지 않은 탐지입니다."
        >
          심각도 미분류 <span className="tabular-nums">{summary.unknown}</span>
        </p>
      )}
    </div>
  );
}
/** 예제·테스트에서 나온 탐지는 본체 숫자와 따로 한 줄로 적는다. */
export function ExampleNote({
  summary,
}: {
  summary?: components["schemas"]["ScopeSummary"] | null;
}) {
  const example = summary?.example;
  if (!example?.total) return null;
  const levels = (
    [
      ["Critical", example.critical],
      ["High", example.high],
      ["Medium", example.medium],
    ] as const
  ).filter(([, count]) => count > 0);
  return (
    <p
      className="mt-2 text-xs text-slate-500"
      title="examples/, example/, test/, tests/ 아래에서 나왔거나 그곳에서 모듈을 불러 쓸 때 나온 탐지입니다. 배지와 위 숫자에는 포함되지 않습니다."
    >
      예제에서 <span className="tabular-nums">{example.total}</span>건
      {levels.length > 0 &&
        ` (${levels.map(([label, count]) => `${label} ${count}`).join(" · ")})`}
    </p>
  );
}
export function FileRef({
  path,
  line,
}: {
  path: string;
  line?: number | null;
}) {
  const suffix = line != null ? `:${line}` : "";
  return (
    <span title={`${path}${suffix}`}>
      {path.split("/").pop()}
      {suffix}
    </span>
  );
}
export function FindingView({
  finding: f,
  onOpen,
}: {
  finding: Finding;
  onOpen?: () => void;
}) {
  return (
    <article className="my-3 rounded-lg border border-line bg-white p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge value={f.severity} />
        {f.scope === "EXAMPLE" && (
          <span className="rounded border border-line bg-white px-1.5 py-0.5 text-xs text-slate-500">
            예제
          </span>
        )}
        {f.raw_data?.severity_source === "moduleguard" && (
          <span
            className="text-xs text-slate-400"
            title="검사 도구가 심각도를 주지 않아 ModuleGuard가 규칙의 의미에 따라 지정한 등급입니다."
          >
            ModuleGuard 기준
          </span>
        )}
        <code>{f.rule_id}</code>
        <span className="text-slate-400">{f.scanner}</span>
      </div>
      <DemoLabel demo={f.raw_data?.development_fixture === true} />
      <h3 className="my-3">{f.title}</h3>
      <p className="break-all font-mono text-xs">
        {f.resource}
        {f.resource && f.file_path && " · "}
        {f.file_path &&
          (f.scope === "EXAMPLE" ? (
            // 예제 탐지는 어느 예제인지 알 수 있게 경로를 다 보여 준다.
            `${f.file_path.replace(/^\//, "")}${f.line_start != null ? `:${f.line_start}` : ""}`
          ) : (
            <FileRef path={f.file_path} line={f.line_start} />
          ))}
      </p>
      {f.description && f.description !== f.title && (
        <p className="my-3 whitespace-pre-wrap leading-6">{f.description}</p>
      )}
      <p className="whitespace-pre-wrap text-slate-500">{f.guideline}</p>
      {f.scanner !== "checkov" && (
        <dl className="mt-3 grid gap-2 text-xs">
          {[
            "source",
            "version",
            "ref",
            "confidence",
            "source_entity",
            "sink_entity",
            "sink",
          ].map(
            (k) =>
              f.raw_data?.[k] != null && (
                <div key={k}>
                  <dt className="inline text-slate-400">{k}: </dt>
                  <dd className="inline break-all">{String(f.raw_data[k])}</dd>
                </div>
              ),
          )}
        </dl>
      )}
      {onOpen && (
        <button className="mt-4 font-semibold text-brand" onClick={onOpen}>
          근거 및 영향 경로 보기 →
        </button>
      )}
    </article>
  );
}
