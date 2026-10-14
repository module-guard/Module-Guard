"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Lock, Upload } from "lucide-react";
import { api, unwrap } from "@/api/client";
import { explainScanError } from "@/api/errors";
import type { components } from "@/api/generated/schema";
import { useApi } from "@/api/hooks";
import {
  Badge,
  ExampleNote,
  SeverityBars,
  State,
  Time,
  shortUrl,
} from "./live-shared";
import { Markdown } from "./markdown";
import { ForkButton, StarButton } from "./module-actions";
import { ModuleSettings } from "./module-settings";
import { ModuleUse } from "./module-use";
import { SecurityBadge } from "./security-badge";
type Detail = components["schemas"]["RepositoryDetail"];
type Version = components["schemas"]["VersionDetail"];
type Variable = {
  name: string;
  type?: string | null;
  default?: string | null;
  required?: boolean;
  description?: string | null;
  sensitive?: boolean;
};
const tabs = [
  ["overview", "개요"],
  ["use", "사용하기"],
  ["interface", "입력·출력"],
  ["security", "보안"],
  ["versions", "버전"],
  ["settings", "설정"],
] as const;
const scannerNames: Record<string, string> = {
  checkov: "보안 설정 (Checkov)",
  source_trust: "공급망 신뢰",
  correlation: "상관 분석",
};

function Interface({ version }: { version: Version }) {
  const variables = version.variables as Variable[] | null;
  const outputs = version.outputs as Variable[] | null;
  if (variables == null || outputs == null)
    return (
      <p className="text-slate-500">
        이 버전에서는 입력·출력 정보를 읽지 못했습니다.
      </p>
    );
  return (
    <>
      <h2 className="mb-3">
        입력{" "}
        <span className="font-normal tabular-nums text-slate-400">
          {variables.length}
        </span>
      </h2>
      {variables.length ? (
        <div className="table-wrap panel">
          <table>
            <thead>
              <tr>
                {["이름", "타입", "기본값", "설명"].map((x) => (
                  <th key={x}>{x}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {variables.map((v) => (
                <tr key={v.name}>
                  <td>
                    <code>{v.name}</code>
                    {v.required && (
                      <span className="ml-2 rounded border border-violet-200 bg-violet-50 px-1.5 py-0.5 text-xs text-brand">
                        필수
                      </span>
                    )}
                  </td>
                  <td>
                    <code
                      className="block max-w-56 truncate text-xs"
                      title={v.type || undefined}
                    >
                      {v.type || "—"}
                    </code>
                  </td>
                  <td>
                    <code
                      className="block max-w-48 truncate text-xs"
                      title={v.default || undefined}
                    >
                      {v.required ? "—" : (v.default ?? "—")}
                    </code>
                  </td>
                  <td className="min-w-64 whitespace-normal text-slate-600">
                    {v.description}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-slate-500">입력 변수가 없습니다.</p>
      )}
      <h2 className="mb-3 mt-8">
        출력{" "}
        <span className="font-normal tabular-nums text-slate-400">
          {outputs.length}
        </span>
      </h2>
      {outputs.length ? (
        <div className="table-wrap panel">
          <table>
            <thead>
              <tr>
                {["이름", "설명"].map((x) => (
                  <th key={x}>{x}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {outputs.map((o) => (
                <tr key={o.name}>
                  <td>
                    <code>{o.name}</code>
                  </td>
                  <td className="whitespace-normal text-slate-600">
                    {o.description}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-slate-500">출력이 없습니다.</p>
      )}
    </>
  );
}

function Security({ base, version }: { base: string; version: Version }) {
  const scan = version.scan;
  if (!scan) return <p className="text-slate-500">검사 결과가 없습니다.</p>;
  return (
    <div className="max-w-2xl">
      <SecurityBadge
        criticalRules={version.critical_rules}
        highRules={version.high_rules}
        unknown={scan.summary.unknown}
        version={version.version}
        scannedAt={scan.finished_at || scan.created_at}
      />
      <h2 className="mb-4 mt-6">
        위험도 분포{" "}
        <span className="ml-1 font-normal tabular-nums text-slate-400">
          탐지 {scan.summary.total}
        </span>
      </h2>
      <SeverityBars summary={scan.summary} />
      <ExampleNote summary={scan.scope_summary} />
      {version.scanner_breakdown && (
        <>
          <h2 className="mb-2 mt-8">검사 도구별</h2>
          {Object.entries(version.scanner_breakdown).map(([k, v]) => (
            <p key={k} className="my-2 flex justify-between">
              <span className="text-slate-600">{scannerNames[k] || k}</span>
              <strong className="tabular-nums">{v}</strong>
            </p>
          ))}
        </>
      )}
      <Link
        href={`${base}/scans/${scan.id}`}
        className="mt-6 inline-block font-semibold text-brand"
      >
        상세 보기 →
      </Link>
    </div>
  );
}

function Versions({ base, detail }: { base: string; detail: Detail }) {
  return (
    <ul className="divide-y divide-line border-y border-line">
      {detail.versions.map((v, i) => (
        <li
          key={`${v.version}:${i}`}
          className="flex flex-wrap items-center gap-3 py-3"
        >
          {v.state === "STORED" ? (
            <Link
              href={`${base}?version=${v.version}`}
              className="w-14 font-semibold text-brand"
            >
              {v.version}
            </Link>
          ) : (
            <span className="w-14 font-semibold text-slate-400">
              {v.version}
            </span>
          )}
          {v.version === detail.latest_version && (
            <span className="rounded border border-line bg-white px-1.5 py-0.5 text-xs text-slate-500">
              최신
            </span>
          )}
          <span className="min-w-0 flex-1 text-xs text-slate-500">
            <Time value={v.created_at} />
            {v.commit_hash && ` · ${v.commit_hash.slice(0, 7)}`}
            {v.state === "FAILED" &&
              ` · ${v.failure ? explainScanError(v.failure) : "검사 실패"}`}
          </span>
          {v.state === "STORED" && v.scan ? (
            <>
              <span className="text-xs text-slate-500">
                탐지{" "}
                <span className="tabular-nums">{v.scan.summary.total}</span>
              </span>
              <SecurityBadge
                criticalRules={v.critical_rules}
                highRules={v.high_rules}
                unknown={v.scan.summary.unknown}
              />
            </>
          ) : (
            <Badge value={v.state === "FAILED" ? "FAILED" : "RUNNING"} />
          )}
          {v.scan && v.state !== "STORED" && (
            <Link className="text-brand" href={`${base}/scans/${v.scan.id}`}>
              검사 보기
            </Link>
          )}
        </li>
      ))}
    </ul>
  );
}

export function ModulePage({ owner, name }: { owner: string; name: string }) {
  const query = useSearchParams();
  const requested = query.get("version") || undefined;
  const tab = tabs.some(([key]) => key === query.get("tab"))
    ? query.get("tab")!
    : "overview";
  const r = useApi(
    (s) =>
      unwrap(
        api.GET("/api/repositories/{owner}/{name}", {
          signal: s,
          params: { path: { owner, name }, query: { version: requested } },
        }),
      ),
    `repository:${owner}/${name}:${requested}`,
  );
  if (!r.data)
    return (
      <State
        error={
          r.error && /not found/i.test(r.error)
            ? "모듈을 찾을 수 없습니다. 비공개이거나 없는 모듈입니다."
            : r.error
        }
        retry={r.retry}
      />
    );
  const d = r.data;
  const v = d.version;
  // 설정 탭은 주인에게만 있다. 남이 ?tab=settings로 와도 개요를 본다.
  const shown = tabs.filter(([key]) => key !== "settings" || d.is_owner);
  const active = shown.some(([key]) => key === tab) ? tab : "overview";
  const base = `/@${d.owner}/${d.name}`;
  const href = (key: string) =>
    `${base}?${new URLSearchParams({
      ...(requested ? { version: requested } : {}),
      ...(key === "overview" ? {} : { tab: key }),
    })}`.replace(/\?$/, "");
  const origin =
    d.origin_url && /^https?:\/\//i.test(d.origin_url) ? d.origin_url : null;
  return (
    <div className="page-enter">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="break-all">
            <Link
              className="font-normal text-slate-500 hover:text-brand"
              href={`/@${d.owner}`}
            >
              @{d.owner}
            </Link>
            <span className="font-normal text-slate-500"> / </span>
            {d.name}
          </h1>
          {d.description && (
            <p className="mt-2 text-slate-600">{d.description}</p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StarButton key={`star:${d.owner}/${d.name}`} detail={d} />
          <ForkButton key={`fork:${d.owner}/${d.name}`} detail={d} />
          {d.is_owner && (
            <Link
              href={`/new?repository=${encodeURIComponent(d.name)}`}
              className="flex h-9 items-center gap-1.5 whitespace-nowrap rounded-lg border border-line bg-white px-3.5 text-sm font-semibold hover:bg-slate-50"
            >
              <Upload size={15} />새 버전 올리기
            </Link>
          )}
        </div>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-500">
        {v?.state === "STORED" && v.scan && (
          <SecurityBadge
            criticalRules={v.critical_rules}
            highRules={v.high_rules}
            unknown={v.scan.summary.unknown}
            version={v.version}
            scannedAt={v.scan.finished_at || v.scan.created_at}
          />
        )}
        {d.visibility === "PRIVATE" && (
          <span className="inline-flex items-center gap-1">
            <Lock size={12} />
            비공개
          </span>
        )}
        {d.provider && <span>{d.provider}</span>}
        {v?.license && <span>{v.license}</span>}
        {d.origin_type === "FORK" &&
          (d.forked_from ? (
            <span>
              원본:{" "}
              <Link
                className="text-brand"
                href={`/@${d.forked_from.owner}/${d.forked_from.name}`}
              >
                @{d.forked_from.owner}/{d.forked_from.name}
              </Link>
            </span>
          ) : (
            <span>다른 모듈에서 가져옴</span>
          ))}
        {origin && d.origin_type !== "FORK" && (
          <span>
            {/* 미러: 서비스가 원본 저장소를 그대로 옮겨 둔 것. */}
            {d.mirror && (
              <span className="mr-1.5 rounded bg-slate-100 px-1.5 py-0.5 font-semibold text-slate-700">
                미러
              </span>
            )}
            {d.mirror ? "· " : ""}원본:{" "}
            <a
              className="text-brand"
              href={origin}
              target="_blank"
              rel="noopener noreferrer nofollow"
            >
              {shortUrl(origin)}
            </a>
            {v?.source_ref && <span> @ {v.source_ref}</span>}
          </span>
        )}
      </div>
      {v && v.state !== "STORED" && (
        <p
          role="status"
          className="mt-5 rounded bg-amber-50 px-4 py-3 text-amber-900"
        >
          {v.state === "FAILED"
            ? `검사에 실패해 등록되지 않았습니다. ${v.failure ? explainScanError(v.failure) : ""}`
            : "검사가 진행 중입니다. 끝나면 등록됩니다."}{" "}
          {v.scan && (
            <Link
              className="font-semibold underline"
              href={`${base}/scans/${v.scan.id}`}
            >
              검사 보기
            </Link>
          )}
        </p>
      )}
      {v && d.latest_version && v.version !== d.latest_version && (
        <p className="mt-5 rounded bg-slate-100 px-4 py-3 text-slate-600">
          {v.version}을 보고 있습니다.{" "}
          <Link className="font-semibold text-brand" href={base}>
            최신 버전({d.latest_version}) 보기
          </Link>
        </p>
      )}
      <nav
        className="mb-6 mt-6 flex gap-5 overflow-x-auto border-b border-line"
        aria-label="모듈 탭"
      >
        {shown.map(([key, label]) => (
          <Link
            key={key}
            href={href(key)}
            scroll={false}
            aria-current={active === key ? "page" : undefined}
            className={`whitespace-nowrap border-b-2 px-1 py-3 ${active === key ? "border-brand font-semibold text-brand" : "border-transparent text-slate-500"}`}
          >
            {label}
            {key === "versions" && (
              <span className="ml-1.5 text-xs font-normal tabular-nums text-slate-400">
                {d.versions.length}
              </span>
            )}
          </Link>
        ))}
      </nav>
      {active === "settings" ? (
        <ModuleSettings detail={d} reload={r.retry} />
      ) : active === "versions" ? (
        <Versions base={base} detail={d} />
      ) : !v ? (
        <p className="text-slate-500">아직 등록된 버전이 없습니다.</p>
      ) : active === "use" ? (
        v.state === "STORED" ? (
          <ModuleUse
            key={`${d.owner}/${d.name}@${v.version}`}
            detail={d}
            version={v}
          />
        ) : (
          <p className="text-slate-500">
            등록이 끝난 버전만 사용할 수 있습니다.
          </p>
        )
      ) : active === "interface" ? (
        <Interface version={v} />
      ) : active === "security" ? (
        <Security base={base} version={v} />
      ) : v.readme ? (
        <Markdown>{v.readme}</Markdown>
      ) : (
        <p className="text-slate-500">README가 없습니다.</p>
      )}
    </div>
  );
}
