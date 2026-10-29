"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, ApiError, unwrap } from "@/api/client";
import type { components } from "@/api/generated/schema";
import { useAuth } from "./auth";
import { Source } from "./publish";
import type { Scan } from "./live-shared";
import { Button } from "./ui/button";

type Report = components["schemas"]["UploadReport"];

export function ScanRetry({ scan }: { scan: Scan }) {
  const { user } = useAuth();
  const router = useRouter();
  const [report, setReport] = useState<Report>();
  const [preparing, setPreparing] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [message, setMessage] = useState("");
  const [next, setNext] = useState(scan.retried_by);
  const [newVersion, setNewVersion] = useState(false);
  const [denied, setDenied] = useState(false);
  const owner = scan.repository?.owner;
  const owns =
    !!user && !!owner && user.login.toLowerCase() === owner.toLowerCase();
  const href = (id: string) => `/scans/${encodeURIComponent(id)}`;

  async function refreshAttempt() {
    const current = await unwrap(
      api.GET("/api/scans/{scan_id}", {
        params: { path: { scan_id: scan.id } },
      }),
    );
    if (current.retried_by) {
      // Only offer a destination the current session can actually read.
      await unwrap(
        api.GET("/api/scans/{scan_id}", {
          params: { path: { scan_id: current.retried_by } },
        }),
      );
      setNext(current.retried_by);
      setReport(undefined);
      setPreparing(false);
    }
  }

  async function retry(e: React.FormEvent) {
    e.preventDefault();
    if (lock.current || !report || !owns) return;
    if (Date.parse(report.expires_at) <= Date.now()) {
      setReport(undefined);
      setAcknowledged(false);
      setMessage(
        "준비한 소스가 만료되었습니다. 같은 출처의 소스를 다시 확인해 주세요.",
      );
      return;
    }
    lock.current = true;
    setBusy(true);
    setMessage("");
    try {
      const result = await unwrap(
        api.POST("/api/scans/{scan_id}/retry", {
          params: { path: { scan_id: scan.id } },
          body: {
            upload_id: report.upload_id,
            acknowledge_warnings: acknowledged,
          },
        }),
      );
      setNext(result.scan_id);
      setPreparing(false);
      router.push(href(result.scan_id));
    } catch (cause) {
      const error =
        cause instanceof ApiError
          ? cause
          : new ApiError(0, "요청 결과를 확인할 수 없습니다.");
      if (error.code === "StagedUploadNotFoundError") {
        setReport(undefined);
        setAcknowledged(false);
        setMessage(
          "준비한 소스가 만료되었거나 사용되었습니다. 소스를 다시 확인해 주세요.",
        );
      } else if (error.code === "SourceChanged") {
        setNewVersion(true);
        setMessage(
          "등록 당시와 내용이 달라졌습니다. 재시도 대신 새 버전으로 올려 주세요.",
        );
      } else if (error.code === "SourceMismatch") {
        setReport(undefined);
        setAcknowledged(false);
        setMessage(
          "원래 등록한 출처와 다릅니다. 같은 GitHub 주소·Registry 또는 원래 zip으로 다시 확인해 주세요.",
        );
      } else if (
        error.status === 401 ||
        error.status === 403 ||
        error.status === 404
      ) {
        setDenied(true);
        setMessage(
          "로그인 또는 접근 권한을 확인한 뒤 페이지를 새로고침해 주세요.",
        );
      } else if (error.status === 429) {
        setMessage("진행 중인 검사가 많습니다. 완료된 뒤 다시 시도해 주세요.");
      } else if (error.status === 503) {
        setMessage("검사 대기열이 가득 찼습니다. 잠시 후 다시 시도해 주세요.");
      } else if (error.status === 409) {
        setMessage(
          "이미 재시도되었거나 재시도할 수 없는 상태입니다. 이력을 확인하고, 무결성 불일치 버전은 새 버전으로 올려 주세요.",
        );
        try {
          await refreshAttempt();
        } catch {
          /* Preserve the conflict notice. */
        }
      } else if (error.status === 0) {
        setMessage(
          "응답을 확인하지 못했습니다. 중복 실행을 피하려면 시도 이력을 확인해 주세요.",
        );
        try {
          await refreshAttempt();
        } catch {
          /* A later refresh can reconcile the attempt. */
        }
      } else {
        setMessage(
          error.code === "WarningsNotAcknowledged"
            ? "경고 항목이 비밀값이 아님을 확인해 주세요."
            : "입력값을 확인하고 소스를 다시 준비해 주세요.",
        );
      }
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }

  if (!scan.retry_of && !next && !(owns && scan.status === "FAILED"))
    return null;
  return (
    <section className="panel my-4 p-5" aria-label="검사 시도 이력">
      <h2>검사 시도 이력</h2>
      <div className="my-3 flex gap-4">
        {scan.retry_of && (
          <Link className="text-brand" href={href(scan.retry_of)}>
            이전 검사 보기
          </Link>
        )}
        {next && (
          <Link className="text-brand" href={href(next)}>
            다음 검사 보기
          </Link>
        )}
      </div>
      {message && (
        <p role="alert" className="my-3 text-amber-800">
          {message}
        </p>
      )}
      {owns && !next && scan.status === "FAILED" && !denied && (
        <>
          {newVersion || scan.integrity?.integrity_status === "MISMATCH" ? (
            <p>
              무결성 불일치 또는 변경된 소스는 새 버전으로 등록해야 합니다.{" "}
              <Link
                className="text-brand"
                href={`/new?repository=${encodeURIComponent(scan.repository!.name)}`}
              >
                새 버전 올리기
              </Link>
            </p>
          ) : !preparing ? (
            <Button onClick={() => setPreparing(true)}>
              실패한 검사 다시 시도
            </Button>
          ) : !report ? (
            <>
              <p>
                등록 당시와 같은 출처·내용으로 소스를 다시 준비해 주세요. 기존
                실패 기록은 보존됩니다.
              </p>
              <Source
                url={
                  scan.module.source_url.startsWith("https://github.com/")
                    ? scan.module.source_url
                    : ""
                }
                onReady={(value) => {
                  setReport(value);
                  setAcknowledged(false);
                  setMessage("");
                }}
              />
            </>
          ) : (
            <form onSubmit={retry}>
              <p>
                {report.file_count}개 파일 준비 완료 ·{" "}
                {report.origin_url || "zip 업로드"}
              </p>
              {report.warnings.length > 0 && (
                <div className="my-3">
                  <p>
                    비밀값 의심 항목 {report.warnings.length}건을 확인해 주세요.
                  </p>
                  <ul>
                    {report.warnings.map((warning, i) => (
                      <li key={i}>
                        {warning.file}:{warning.line} {warning.preview}
                      </li>
                    ))}
                  </ul>
                  <label>
                    <input
                      type="checkbox"
                      checked={acknowledged}
                      onChange={(e) => setAcknowledged(e.target.checked)}
                      disabled={busy}
                    />{" "}
                    비밀값이 아님을 확인했습니다
                  </label>
                </div>
              )}
              <div className="mt-3 flex gap-3">
                <Button
                  type="submit"
                  disabled={
                    busy || (report.warnings.length > 0 && !acknowledged)
                  }
                >
                  {busy ? "재시도 요청 중…" : "검사 다시 시작"}
                </Button>
                <Button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setReport(undefined);
                    setAcknowledged(false);
                  }}
                >
                  소스 다시 준비
                </Button>
              </div>
            </form>
          )}
        </>
      )}
    </section>
  );
}
