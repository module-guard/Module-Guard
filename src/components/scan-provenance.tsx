"use client";

import { useState } from "react";
import type { Scan } from "./live-shared";

type RecordValue = Record<string, unknown>;
const record = (value: unknown): RecordValue | null =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as RecordValue)
    : null;
const rules = {
  rules_version: "통합 규칙 버전",
  severity_rules_version: "위험도 규칙 버전",
  remediation_rules_version: "조치 안내 규칙 버전",
};
const versions = {
  scanner_name: "분석 도구",
  checkov_version: "Checkov 버전",
  moduleguard_version: "ModuleGuard 버전",
  build_commit: "ModuleGuard 빌드 커밋",
  ...rules,
};
const optionNames = [
  "checkov_timeout_seconds",
  "max_process_output_mb",
  "max_repository_size_mb",
  "max_analysis_entities",
  "max_analysis_edges",
  "max_local_module_depth",
  "max_terraform_file_bytes",
  "max_flow_edges",
  "max_flow_paths",
  "max_flow_depth",
  "max_flow_search_steps",
];
const checkovNames = [
  "skip_download",
  "download_external_modules",
  "evaluate_variables",
  "repository_config_isolated",
];
const text = (value: unknown) =>
  typeof value === "string" && value.trim() ? value : null;
function pickVersions(source: RecordValue, names: Record<string, string>) {
  return Object.fromEntries(
    Object.keys(names).map((key) => [key, text(source[key])]),
  );
}
function processing(source: RecordValue) {
  const date = text(source.applied_at);
  return {
    applied_at:
      date && Number.isFinite(Date.parse(date))
        ? new Date(date).toISOString()
        : null,
    ...pickVersions(source, rules),
  };
}

// The API exposes dictionaries. Select known public fields instead of copying raw responses.
export function publicProvenance(
  scan: Pick<Scan, "provenance" | "postprocessing">,
): {
  provenance:
    | (RecordValue & {
        analysis_options: Record<string, number>;
        checkov_options: Record<string, string | boolean>;
      })
    | null;
  postprocessing: (RecordValue & { history: RecordValue[] }) | null;
} {
  const p = record(scan.provenance);
  const post = record(scan.postprocessing);
  const options = record(p?.analysis_options);
  const checkov = record(p?.checkov_options);
  return {
    provenance: p
      ? {
          ...pickVersions(p, versions),
          analysis_options: Object.fromEntries(
            optionNames.flatMap((key) => {
              const value = options?.[key];
              return typeof value === "number" &&
                Number.isSafeInteger(value) &&
                value >= 0
                ? [[key, value]]
                : [];
            }),
          ),
          checkov_options: {
            ...(text(checkov?.skip_path)
              ? { skip_path: text(checkov?.skip_path)! }
              : {}),
            ...(checkov?.framework === "terraform"
              ? { framework: "terraform" }
              : {}),
            ...Object.fromEntries(
              checkovNames.flatMap((key) =>
                typeof checkov?.[key] === "boolean"
                  ? [[key, checkov[key]]]
                  : [],
              ),
            ),
          },
        }
      : null,
    postprocessing: post
      ? {
          ...processing(post),
          history: Array.isArray(post.history)
            ? post.history.flatMap((item) => {
                const entry = record(item);
                return entry ? [processing(entry)] : [];
              })
            : [],
        }
      : null,
  };
}

function Fields({
  values,
  labels,
}: {
  values: RecordValue;
  labels: Record<string, string>;
}) {
  return (
    <dl className="grid min-w-0 gap-3 sm:grid-cols-2">
      {Object.entries(labels).map(([key, label]) => (
        <div key={key} className="min-w-0">
          <dt className="text-sm text-slate-500">{label}</dt>
          <dd className="mt-1 break-all font-mono text-sm">
            {values[key] == null
              ? key === "checkov_version"
                ? "기록 없음 — 수집 실패 또는 미수집"
                : "기록 없음"
              : String(values[key])}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function ScanProvenance({
  scan,
}: {
  scan: Pick<Scan, "provenance" | "postprocessing">;
}) {
  const data = publicProvenance(scan);
  const [copyStatus, setCopyStatus] = useState("");
  return (
    <section
      aria-label="분석 환경·후처리 이력"
      className="my-6 min-w-0 rounded-lg border border-line bg-white p-4"
    >
      <details>
        <summary className="min-h-11 cursor-pointer rounded py-2 font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand">
          분석 환경·후처리 이력
        </summary>
        <div className="mt-3 space-y-5">
          <p className="text-sm text-slate-600">
            검사 당시 기록된 도구·규칙 버전입니다. 규칙 해시는 변경 식별용이며
            소스·ZIP 해시 무결성 검증과 별개입니다. 현재 서버 버전으로 보완하지
            않습니다.
          </p>
          <div>
            <h3 className="mb-3 font-semibold">최초 검사 분석 환경</h3>
            {data.provenance ? (
              <>
                <Fields values={data.provenance} labels={versions} />
                <h4 className="my-3 font-semibold">공개 분석 옵션</h4>
                {Object.entries({
                  analysis_options: data.provenance.analysis_options,
                  checkov_options: data.provenance.checkov_options,
                }).map(([name, options]) => (
                  <div key={name} className="mt-3">
                    <p className="mb-2 text-sm">
                      {name === "analysis_options"
                        ? "분석 한도·시간 제한"
                        : "Checkov 실행 옵션"}
                    </p>
                    {Object.keys(options).length ? (
                      <Fields
                        values={options}
                        labels={Object.fromEntries(
                          Object.keys(options).map((key) => [key, key]),
                        )}
                      />
                    ) : (
                      <p>기록 없음</p>
                    )}
                  </div>
                ))}
              </>
            ) : (
              <p>
                기록 없음 — 이전 검사이거나 분석 환경 메타데이터가 저장되지
                않았습니다.
              </p>
            )}
          </div>
          <div>
            <h3 className="mb-3 font-semibold">후처리 이력 (reapply)</h3>
            <p className="mb-3 text-sm text-slate-600">
              최초 검사와 별도로 기록됩니다. 후처리는 상관 위험을 재분석하지
              않습니다. 시각은 UTC 기준입니다.
            </p>
            {data.postprocessing ? (
              <ol className="space-y-4">
                {[...data.postprocessing.history, data.postprocessing].map(
                  (entry, index, entries) => (
                    <li key={index} className="rounded border border-line p-3">
                      <h4 className="mb-3 font-semibold">
                        {index === entries.length - 1
                          ? "최근 후처리"
                          : `이전 후처리 ${index + 1}`}
                      </h4>
                      <Fields
                        values={entry}
                        labels={{ applied_at: "적용 시각 (UTC)", ...rules }}
                      />
                    </li>
                  ),
                )}
              </ol>
            ) : (
              <p>기록 없음 — 저장된 후처리 이력이 없습니다.</p>
            )}
          </div>
          <button
            type="button"
            className="min-h-11 rounded border border-line px-3 text-brand focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(
                  JSON.stringify(data, null, 2),
                );
                setCopyStatus("복사됨");
              } catch {
                setCopyStatus(
                  "복사하지 못했습니다. 브라우저의 클립보드 권한을 확인해 주세요.",
                );
              }
            }}
          >
            분석 환경·후처리 이력 복사
          </button>
          <p role="status" aria-live="polite" className="text-sm">
            {copyStatus}
          </p>
        </div>
      </details>
    </section>
  );
}
