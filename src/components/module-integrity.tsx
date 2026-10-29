"use client";
import { useState } from "react";
import { api, unwrap } from "@/api/client";
import { useApi } from "@/api/hooks";
import type { components } from "@/api/generated/schema";

type Info = components["schemas"]["IntegrityInfo"];
const labels = {
  VERIFIED: "정상",
  MISMATCH: "불일치",
  UNVERIFIED: "미검증",
  ERROR: "검증 오류",
};

export function IntegrityBadge({ info }: { info?: Info }) {
  return (
    <span className="text-xs text-slate-500">
      무결성: {labels[info?.integrity_status || "UNVERIFIED"]}
    </span>
  );
}

export function IntegritySummary({ info }: { info?: Info }) {
  const [copied, setCopied] = useState("");
  return (
    <section aria-label="모듈 무결성" className="panel my-5 space-y-3 p-5">
      <h2>원본 무결성 · {labels[info?.integrity_status || "UNVERIFIED"]}</h2>
      {info?.zip_sha256 && (
        <p className="text-sm">
          기본 ZIP 무결성 ·{" "}
          {labels[info.artifact_integrity_status || "UNVERIFIED"]}
        </p>
      )}
      <dl className="space-y-2 text-sm">
        {[
          ["원본 Commit SHA", info?.source_commit_sha],
          ["Manifest SHA-256", info?.manifest_sha256],
          ["원본 tar.gz SHA-256", info?.snapshot_sha256],
          ["기본 다운로드 ZIP SHA-256", info?.zip_sha256],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-slate-500">{label}</dt>
            <dd className="break-all font-mono">
              {value || "아직 없음"}{" "}
              {value && (
                <button
                  type="button"
                  className="ml-2 text-brand"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(value);
                      setCopied(label || "");
                    } catch {
                      setCopied("복사 실패");
                    }
                  }}
                >
                  복사
                </button>
              )}
            </dd>
          </div>
        ))}
        <div>
          <dt className="text-slate-500">마지막 검증</dt>
          <dd>
            {info?.verified_at
              ? new Date(info.verified_at).toLocaleString("ko-KR")
              : "미검증"}
          </dd>
        </div>
      </dl>
      {copied && (
        <p role="status" className="text-xs">
          {copied === "복사 실패" ? copied : `${copied} 복사 완료`}
        </p>
      )}
      {!!info?.mismatched_files?.length && (
        <p role="alert">변경된 파일: {info.mismatched_files.join(", ")}</p>
      )}
      <p className="text-xs text-slate-500">
        SHA-256 일치는 파일의 동일성을 확인합니다. 코드의 안전성이나 게시자의
        신뢰성을 보증하지 않습니다. 비밀정보·Git 메타데이터·Terraform 캐시는
        기존 정책에 따라 제외됩니다.
      </p>
    </section>
  );
}

export function ModuleIntegrity({
  owner,
  name,
  version,
}: {
  owner: string;
  name: string;
  version?: string | null;
}) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [files, setFiles] =
    useState<components["schemas"]["IntegrityFile"][]>();
  const params = {
    path: { owner, name },
    query: { version: version || undefined },
  };
  const r = useApi(
    (signal) =>
      unwrap(
        api.GET("/api/repositories/{owner}/{name}/integrity", {
          params,
          signal,
        }),
      ),
    `integrity:${owner}/${name}:${version}`,
  );
  return (
    <div>
      <IntegritySummary info={r.data} />
      <div className="mb-5 flex flex-wrap gap-4 text-sm">
        <button
          type="button"
          disabled={busy}
          className="text-brand disabled:opacity-50"
          onClick={async () => {
            setBusy(true);
            setMessage("");
            try {
              await unwrap(
                api.POST("/api/repositories/{owner}/{name}/integrity/verify", {
                  params,
                }),
              );
              setMessage("무결성 검증 완료");
            } catch (e) {
              setMessage(e instanceof Error ? e.message : "무결성 검증 실패");
            } finally {
              r.retry();
              setBusy(false);
            }
          }}
        >
          {busy ? "검증 중…" : "무결성 다시 검사"}
        </button>
        <button
          type="button"
          className="text-brand"
          onClick={async () => {
            try {
              setFiles(
                await unwrap(
                  api.GET("/api/repositories/{owner}/{name}/integrity/files", {
                    params,
                  }),
                ),
              );
            } catch {
              setMessage("파일별 해시를 읽지 못했습니다.");
            }
          }}
        >
          파일별 해시 확인
        </button>
      </div>
      {(message || r.error) && (
        <p role="status" className="mb-4 text-sm">
          {message || r.error}
        </p>
      )}
      {files && (
        <details open className="panel mb-5 p-4">
          <summary>파일별 SHA-256 ({files.length})</summary>
          <ul className="max-h-72 space-y-2 overflow-auto text-xs">
            {files.map((f) => (
              <li key={f.path}>
                <strong>{f.path}</strong> · {f.size} bytes
                <code className="block break-all">{f.sha256}</code>
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(f.sha256);
                      setMessage("파일 해시 복사 완료");
                    } catch {
                      setMessage("복사 실패");
                    }
                  }}
                >
                  복사
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
