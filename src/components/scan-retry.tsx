"use client";
import { useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { api, ApiError, unwrap } from "@/api/client";
import { useApi } from "@/api/hooks";
import { loginUrl, useAuth } from "./auth";
import { Panel, shortUrl, type Scan } from "./live-shared";
import {
  Rejection,
  ReportCheck,
  Source,
  type Report,
  type SourceKind,
} from "./publish";
import { Button } from "./ui/button";

type Repository = NonNullable<Scan["repository"]>;

/** 같은 저장소 안의 다른 시도 주소. */
export function attemptHref(repository: Repository, scanId: string) {
  return `/@${repository.owner}/${repository.name}/scans/${scanId}`;
}

/** 이전·다음 시도 링크. 볼 수 있는지는 서버가 그 검사를 열 때 다시 확인한다. */
export function AttemptLinks({ scan }: { scan: Scan }) {
  const repository = scan.repository;
  if (!repository || (!scan.retry_of && !scan.retried_by)) return null;
  return (
    <nav aria-label="검사 시도" className="flex flex-wrap gap-4 text-sm">
      {scan.retry_of && (
        <Link
          className="flex items-center gap-1 text-brand"
          href={attemptHref(repository, scan.retry_of)}
        >
          <ArrowLeft size={14} />
          이전 시도
        </Link>
      )}
      {scan.retried_by && (
        <Link
          className="flex items-center gap-1 text-brand"
          href={attemptHref(repository, scan.retried_by)}
        >
          다음 시도
          <ArrowRight size={14} />
        </Link>
      )}
    </nav>
  );
}

/**
 * 재시도 오류를 사용자가 할 수 있는 다음 행동으로 바꾼다.
 * - source: 소스를 새로 준비해야 한다(만료·출처 불일치).
 * - review: 준비한 소스는 그대로 두고 잠시 뒤 다시 누르면 된다.
 * - conflict: 이미 다시 시도됐거나 더는 재시도할 수 없는 검사다.
 * - stop: 이 화면에서 더 할 수 있는 일이 없다(권한·로그인·내용 변경).
 */
export type RetryProblem = {
  next: "source" | "review" | "conflict" | "stop";
  message: string;
  login?: boolean;
  newVersion?: boolean;
};

export function explainRetryError(error: ApiError): RetryProblem {
  if (error.code === "StagedUploadNotFoundError" || error.status === 410)
    return {
      next: "source",
      message: "준비한 소스가 만료되었습니다. 소스를 다시 올려 주세요.",
    };
  if (error.code === "SourceMismatch")
    return {
      next: "source",
      message:
        "원래 등록한 출처와 같은 소스로만 다시 시도할 수 있습니다. 원래 출처에서 다시 준비해 주세요.",
    };
  if (error.code === "SourceChanged")
    return {
      next: "stop",
      message:
        "등록 당시와 내용이 달라져 이 검사는 다시 시도할 수 없습니다. 새 버전으로 올려 주세요.",
      newVersion: true,
    };
  if (error.code === "WarningsNotAcknowledged")
    return {
      next: "review",
      message:
        "확인이 필요한 항목이 있습니다. 비밀값이 아님을 확인한 뒤 다시 눌러 주세요.",
    };
  switch (error.status) {
    case 409:
      return {
        next: "conflict",
        message: error.message || "이미 다시 시도된 검사입니다.",
      };
    case 429:
      return {
        next: "review",
        message:
          "진행 중인 올리기가 많습니다. 진행 중인 검사가 끝난 뒤 다시 눌러 주세요.",
      };
    case 503:
      return {
        next: "review",
        message:
          "검사 대기열이 가득 찼습니다. 준비한 소스는 그대로 두었으니 잠시 뒤 다시 눌러 주세요.",
      };
    case 401:
      return {
        next: "stop",
        message: "로그인이 만료되었습니다. 다시 로그인해 주세요.",
        login: true,
      };
    case 403:
      return {
        next: "stop",
        message:
          "허용되지 않은 위치에서 보낸 요청입니다. 페이지를 새로 고친 뒤 다시 시도해 주세요.",
      };
    case 404:
      return {
        next: "stop",
        message: "이 검사를 찾을 수 없거나 다시 시도할 권한이 없습니다.",
      };
    case 0:
      return { next: "review", message: error.message };
    default:
      return {
        next: "review",
        message: `다시 시도하지 못했습니다 (${error.status}). 잠시 뒤 다시 눌러 주세요.`,
      };
  }
}

/** 원래 출처와 같은 종류만 고를 수 있다. Registry 가져오기도 GitHub 출처로 기록된다. */
function sourceKinds(originType: string): readonly SourceKind[] {
  return originType === "UPLOAD" ? ["zip"] : ["github", "registry"];
}

export function expired(report: Report, now = Date.now()) {
  return new Date(report.expires_at).getTime() <= now;
}

/** 실패한 검사를 소유자가 다시 실행하는 화면. 소유자가 아니면 아무것도 그리지 않는다. */
export function ScanRetry({ scan }: { scan: Scan }) {
  const { user } = useAuth();
  const repository = scan.repository;
  if (
    scan.status !== "FAILED" ||
    !repository ||
    scan.retried_by ||
    !user ||
    user.login.toLowerCase() !== repository.owner.toLowerCase()
  )
    return null;
  return <OwnerRetry key={scan.id} scan={scan} repository={repository} />;
}

function OwnerRetry({
  scan,
  repository,
}: {
  scan: Scan;
  repository: Repository;
}) {
  // 소유 여부와 원래 출처는 서버가 알려 준 값을 쓴다(주소의 로그인 이름만 믿지 않는다).
  const detail = useApi(
    (s) =>
      unwrap(
        api.GET("/api/repositories/{owner}/{name}", {
          signal: s,
          params: { path: { owner: repository.owner, name: repository.name } },
        }),
      ),
    `retry-repository:${repository.owner}/${repository.name}`,
  );
  if (!detail.data?.is_owner) return null;
  const newVersionHref = `/new?repository=${encodeURIComponent(repository.name)}`;
  if (scan.integrity?.integrity_status === "MISMATCH")
    return (
      <Panel title="다시 시도">
        <p>
          무결성 불일치가 확인된 버전은 다시 시도할 수 없습니다.{" "}
          <Link className="font-semibold text-brand" href={newVersionHref}>
            새 버전으로 올리기
          </Link>
        </p>
      </Panel>
    );
  return (
    <RetryForm
      scan={scan}
      repository={repository}
      originType={detail.data.origin_type}
      originUrl={detail.data.origin_url}
      newVersionHref={newVersionHref}
    />
  );
}

function RetryForm({
  scan,
  repository,
  originType,
  originUrl,
  newVersionHref,
}: {
  scan: Scan;
  repository: Repository;
  originType: string;
  originUrl: string | null | undefined;
  newVersionHref: string;
}) {
  const router = useRouter();
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const [report, setReport] = useState<Report>();
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<RetryProblem & { error: ApiError }>();
  const [existing, setExisting] = useState<string>();
  // 상태 갱신 전에 눌린 두 번째 클릭도 막는다.
  const inFlight = useRef(false);
  // 소스를 새로 받을 때마다 준비 폼을 처음 상태로 되돌린다.
  const [sourceKey, setSourceKey] = useState(0);

  function resetSource() {
    setReport(undefined);
    setAcknowledged(false);
    setSourceKey((k) => k + 1);
  }

  function backToSource(next: RetryProblem, error: ApiError) {
    resetSource();
    setProblem({ ...next, error });
  }

  async function submit() {
    if (!report || inFlight.current) return;
    if (expired(report)) {
      const error = new ApiError(404, "", "StagedUploadNotFoundError");
      backToSource(explainRetryError(error), error);
      return;
    }
    inFlight.current = true;
    setBusy(true);
    setProblem(undefined);
    try {
      const accepted = await unwrap(
        api.POST("/api/scans/{scan_id}/retry", {
          params: { path: { scan_id: scan.id } },
          body: {
            upload_id: report.upload_id,
            acknowledge_warnings: acknowledged,
          },
        }),
      );
      router.push(attemptHref(repository, accepted.scan_id));
      // 이동이 끝날 때까지 버튼을 잠가 둔다.
      return;
    } catch (e) {
      const error = e instanceof ApiError ? e : new ApiError(0, String(e));
      const next = explainRetryError(error);
      if (next.next === "source") backToSource(next, error);
      else setProblem({ ...next, error });
      if (next.next === "conflict") {
        // 이미 다시 시도됐다면 그 시도로 안내한다.
        const latest = await unwrap(
          api.GET("/api/scans/{scan_id}", {
            params: { path: { scan_id: scan.id } },
          }),
        ).catch(() => undefined);
        if (latest?.retried_by) setExisting(latest.retried_by);
      }
      inFlight.current = false;
      setBusy(false);
    }
  }

  const blocked = problem?.next === "conflict" || problem?.next === "stop";
  const needsAck = (report?.warnings.length ?? 0) > 0;
  return (
    <Panel title="다시 시도">
      <p className="text-slate-600">
        원래 등록한 출처와 같은 소스를 다시 준비해 검사를 새로 실행합니다. 이번
        검사 기록은 그대로 남고 새 시도와 연결됩니다.
      </p>
      <p className="mt-1 text-xs text-slate-500">
        원래 출처:{" "}
        {originType === "UPLOAD"
          ? "zip 파일 업로드"
          : originUrl
            ? shortUrl(originUrl)
            : "GitHub"}
      </p>
      {problem && (
        <div
          role="alert"
          className="mt-4 rounded bg-red-50 px-4 py-3 text-red-800"
        >
          <p className="font-semibold">{problem.message}</p>
          {existing && (
            <Link
              className="mt-2 inline-block font-semibold text-brand"
              href={attemptHref(repository, existing)}
            >
              이미 시작된 새 시도 보기
            </Link>
          )}
          {problem.login && (
            <a
              className="mt-2 inline-block font-semibold text-brand"
              href={loginUrl(path)}
            >
              GitHub로 로그인
            </a>
          )}
          {problem.newVersion && (
            <Link
              className="mt-2 inline-block font-semibold text-brand"
              href={newVersionHref}
            >
              새 버전으로 올리기
            </Link>
          )}
        </div>
      )}
      {blocked ? null : !open ? (
        <Button type="button" className="mt-4" onClick={() => setOpen(true)}>
          다시 시도하기
        </Button>
      ) : report ? (
        <div className="mt-5">
          <ReportCheck
            report={report}
            acknowledged={acknowledged}
            onAcknowledge={setAcknowledged}
          />
          {problem?.next === "review" && problem.error.details.length > 0 && (
            <Rejection error={problem.error} />
          )}
          <div className="mt-5 flex gap-3">
            <Button
              type="button"
              disabled={busy || (needsAck && !acknowledged)}
              aria-busy={busy}
              onClick={submit}
            >
              {busy ? "다시 시도하는 중…" : "이 소스로 다시 시도"}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={() => {
                setProblem(undefined);
                resetSource();
              }}
            >
              소스 다시 준비
            </Button>
          </div>
        </div>
      ) : (
        <Source
          key={sourceKey}
          url={originUrl ?? ""}
          kinds={sourceKinds(originType)}
          lockUrl={originType !== "UPLOAD" && !!originUrl}
          submitLabel="소스 준비"
          onReady={(next) => {
            setProblem(undefined);
            setAcknowledged(false);
            setReport(next);
          }}
        />
      )}
    </Panel>
  );
}
