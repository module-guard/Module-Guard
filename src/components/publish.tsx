"use client";
import { IntegrityBadge } from "./module-integrity";
import { useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { FileArchive, Github } from "lucide-react";
import { api, ApiError, slowApi, unwrap } from "@/api/client";
import { explainScanError } from "@/api/errors";
import type { components } from "@/api/generated/schema";
import { useApi } from "@/api/hooks";
import { loginUrl, useAuth } from "./auth";
import { Badge, Time, shortUrl } from "./live-shared";
import { Button } from "./ui/button";
type Report = components["schemas"]["UploadReport"];
const MAX_ZIP_MB = 10;
const NAME = /^[a-z0-9][a-z0-9_-]{0,99}$/;

function Rejection({ error }: { error: ApiError }) {
  return (
    <div role="alert" className="mt-4 rounded bg-red-50 px-4 py-3 text-red-800">
      <p className="font-semibold">{error.message}</p>
      {error.details.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs">
          {error.details.map((d, i) => (
            <li key={i} className="break-all">
              <code>
                {d.path}
                {d.line != null && `:${d.line}`}
              </code>
              {d.reason && ` — ${d.reason}`}
              {d.preview && (
                <>
                  {" "}
                  <code className="rounded bg-white px-1">{d.preview}</code>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Source({
  url: initialUrl = "",
  onReady,
}: {
  url?: string;
  onReady: (report: Report) => void;
}) {
  const [kind, setKind] = useState<"github" | "zip" | "registry">("github");
  const [url, setUrl] = useState(initialUrl);
  const [registryAddress, setRegistryAddress] = useState("");
  const [registryVersion, setRegistryVersion] = useState("");
  const [file, setFile] = useState<File>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError>();
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  function choose(candidate?: File) {
    setError(undefined);
    if (!candidate) return;
    if (candidate.size > MAX_ZIP_MB * 1024 * 1024) {
      setFile(undefined);
      setError(
        new ApiError(413, `zip 파일은 ${MAX_ZIP_MB}MB까지 올릴 수 있습니다.`),
      );
      return;
    }
    setFile(candidate);
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      onReady(
        kind === "registry"
          ? await unwrap(
              slowApi.POST("/api/uploads/registry", {
                body: {
                  address: registryAddress,
                  version: registryVersion || null,
                },
              }),
            )
          : kind === "github"
            ? await unwrap(
                slowApi.POST("/api/uploads/github", { body: { url } }),
              )
            : await unwrap(
                slowApi.POST("/api/uploads/zip", {
                  params: { query: { filename: file!.name } },
                  // 본문이 zip 그 자체다. 스키마에 본문 정의가 없어 형 변환한다.
                  body: file as never,
                  bodySerializer: (body: unknown) => body as Blob,
                  headers: { "Content-Type": "application/zip" },
                } as never),
              ),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="mt-6">
      <div className="flex gap-5 border-b border-line" role="tablist">
        {(
          [
            ["github", "GitHub 주소", Github],
            ["zip", "zip 파일", FileArchive],
            ["registry", "Terraform Registry", FileArchive],
          ] as const
        ).map(([value, label, Icon]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={kind === value}
            className={`flex items-center gap-1.5 border-b-2 px-1 py-3 ${kind === value ? "border-brand font-semibold text-brand" : "border-transparent text-slate-500"}`}
            onClick={() => {
              setKind(value);
              setError(undefined);
            }}
          >
            <Icon size={15} />
            {label}
          </button>
        ))}
      </div>
      <div className="mt-5">
        {kind === "registry" ? (
          <>
            <label className="label" htmlFor="registry-address">
              Terraform Registry 주소
            </label>
            <input
              id="registry-address"
              className="input"
              required
              placeholder="terraform-aws-modules/vpc/aws"
              value={registryAddress}
              onChange={(e) => setRegistryAddress(e.target.value)}
            />
            <label className="label mt-3" htmlFor="registry-version">
              버전 (선택)
            </label>
            <input
              id="registry-version"
              className="input"
              placeholder="예: 5.0.0"
              value={registryVersion}
              onChange={(e) => setRegistryVersion(e.target.value)}
            />
          </>
        ) : kind === "github" ? (
          <>
            <label className="label" htmlFor="source-url">
              GitHub 저장소 주소
            </label>
            <input
              id="source-url"
              className="input"
              type="url"
              required
              placeholder="https://github.com/소유자/저장소 (공개 저장소)"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </>
        ) : (
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              choose(e.dataTransfer.files[0]);
            }}
            className={`rounded-lg border-2 border-dashed p-8 text-center ${dragging ? "border-brand bg-[#f8f6ff]" : "border-line bg-white"}`}
          >
            <input
              ref={input}
              id="source-zip"
              type="file"
              accept=".zip,application/zip"
              className="sr-only"
              onChange={(e) => choose(e.target.files?.[0])}
            />
            <label htmlFor="source-zip" className="cursor-pointer">
              <FileArchive size={26} className="mx-auto text-slate-400" />
              <span className="mt-2 block">
                {file ? (
                  <strong>{file.name}</strong>
                ) : (
                  <>
                    zip 파일을 끌어다 놓거나{" "}
                    <span className="text-brand">선택</span>하세요
                  </>
                )}
              </span>
              <span className="mt-1 block text-xs text-slate-500">
                {MAX_ZIP_MB}MB까지 · Terraform 파일(.tf)이 들어 있어야 합니다
              </span>
            </label>
          </div>
        )}
      </div>
      {error && <Rejection error={error} />}
      <Button
        type="submit"
        className="mt-5"
        disabled={busy || (kind === "zip" && !file)}
      >
        {busy
          ? kind === "github"
            ? "가져와서 확인하는 중…"
            : "확인하는 중…"
          : "확인"}
      </Button>
      <p className="mt-3 text-xs text-slate-500">
        올리기 전에 파일 구성과 비밀값을 먼저 확인합니다. 이 단계에서는 아무것도
        공개되지 않습니다.
      </p>
    </form>
  );
}

function Review({
  report,
  name: initialName,
  repository,
  onBack,
}: {
  report: Report;
  name?: string;
  /** 있으면 이 모듈의 새 버전으로 올린다(이름·설명·공개 여부는 그대로). */
  repository?: string;
  onBack: () => void;
}) {
  const router = useRouter();
  const [name, setName] = useState(initialName || report.suggested_name);
  const [description, setDescription] = useState("");
  const [visibility, setVisibility] = useState<"PUBLIC" | "PRIVATE">("PUBLIC");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError>();
  const needsAck = report.warnings.length > 0;
  const validName = NAME.test(name);
  return (
    <form
      className="mt-6"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(undefined);
        try {
          const created = await unwrap(
            repository
              ? api.POST("/api/repositories/{name}/versions", {
                  params: { path: { name: repository } },
                  body: {
                    upload_id: report.upload_id,
                    acknowledge_warnings: acknowledged,
                  },
                })
              : api.POST("/api/repositories", {
                  body: {
                    upload_id: report.upload_id,
                    name,
                    description: description || null,
                    visibility,
                    acknowledge_warnings: acknowledged,
                  },
                }),
          );
          router.push(
            `/@${created.owner}/${created.name}/scans/${created.scan_id}`,
          );
        } catch (e) {
          setError(e instanceof ApiError ? e : new ApiError(0, String(e)));
          setBusy(false);
        }
      }}
    >
      <section aria-label="확인 결과" className="panel p-5">
        <p>
          <strong className="tabular-nums">{report.file_count}</strong>개 파일을
          올립니다{" "}
          <span className="text-xs text-slate-500">
            ({(report.size_bytes / 1024).toFixed(1)}KB
            {report.origin_url && ` · ${shortUrl(report.origin_url)}`}
            {report.commit_hash && ` · ${report.commit_hash.slice(0, 7)}`})
          </span>
        </p>
        {report.excluded_count > 0 && (
          <details className="mt-3">
            <summary className="cursor-pointer text-slate-600">
              제외된 파일 {report.excluded_count}개
            </summary>
            <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto text-xs text-slate-500">
              {report.excluded.map((f) => (
                <li key={f.path} className="break-all">
                  <code>{f.path}</code> — {f.reason}
                </li>
              ))}
            </ul>
          </details>
        )}
        {needsAck && (
          <div className="mt-4 rounded bg-amber-50 px-4 py-3 text-amber-900">
            <p className="font-semibold">
              확인 필요 {report.warnings.length}건
            </p>
            <p className="mt-1 text-xs">
              무작위 문자열처럼 보이는 값입니다. 해시나 예제 값이면 괜찮지만,
              실제 키나 토큰이라면 올리지 마세요.
            </p>
            <ul className="mt-2 space-y-1 text-xs">
              {report.warnings.map((w) => (
                <li key={`${w.file}:${w.line}`} className="break-all">
                  <code>
                    {w.file}:{w.line}
                  </code>
                  {w.preview && (
                    <>
                      {" "}
                      <code className="rounded bg-white px-1">{w.preview}</code>
                    </>
                  )}
                </li>
              ))}
            </ul>
            <label className="mt-3 flex items-center gap-2">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
              />
              비밀값이 아님을 확인했습니다
            </label>
          </div>
        )}
      </section>
      {!repository && (
        <div className="mt-5 space-y-4">
          <div>
            <label className="label" htmlFor="module-name">
              이름
            </label>
            <input
              id="module-name"
              className="input"
              required
              value={name}
              onChange={(e) => setName(e.target.value.toLowerCase())}
              aria-invalid={!validName}
              aria-describedby="module-name-help"
            />
            <p
              id="module-name-help"
              className={`mt-1 text-xs ${validName ? "text-slate-500" : "text-red-700"}`}
            >
              영문 소문자·숫자·-·_ 만 쓸 수 있고, 영문자나 숫자로 시작합니다.
            </p>
          </div>
          <div>
            <label className="label" htmlFor="module-description">
              설명 (선택)
            </label>
            <input
              id="module-description"
              className="input"
              maxLength={500}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          <fieldset>
            <legend className="label">공개 여부</legend>
            <div className="flex gap-5">
              {(
                [
                  ["PUBLIC", "공개", "누구나 보고 가져갈 수 있습니다"],
                  ["PRIVATE", "비공개", "나만 볼 수 있습니다"],
                ] as const
              ).map(([value, label, hint]) => (
                <label key={value} className="flex items-start gap-2">
                  <input
                    type="radio"
                    name="visibility"
                    className="mt-1"
                    checked={visibility === value}
                    onChange={() => setVisibility(value)}
                  />
                  <span>
                    {label}
                    <span className="block text-xs text-slate-500">{hint}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        </div>
      )}
      {error && <Rejection error={error} />}
      <div className="mt-6 flex gap-3">
        <Button
          type="submit"
          disabled={
            busy || (!repository && !validName) || (needsAck && !acknowledged)
          }
        >
          {busy ? "올리는 중…" : repository ? "새 버전으로 올리기" : "올리기"}
        </Button>
        <Button type="button" variant="outline" onClick={onBack}>
          다른 소스 선택
        </Button>
      </div>
    </form>
  );
}

function MyModules({ owner }: { owner: string }) {
  const r = useApi(
    (s) => unwrap(api.GET("/api/me/repositories", { signal: s })),
    "my-repositories",
  );
  const [error, setError] = useState("");
  if (!r.data?.length) return null;
  return (
    <section className="mt-12">
      <h2 className="mb-2">내가 올린 모듈</h2>
      {error && (
        <p role="alert" className="mb-2 text-red-700">
          {error}
        </p>
      )}
      <ul className="divide-y divide-line border-y border-line">
        {r.data.map((m) =>
          m.state === "FAILED" ? (
            // 실패한 올리기는 주인에게만 보인다: 다시 올리거나 지울 수 있다.
            <li key={m.name} className="flex flex-wrap items-center gap-3 py-3">
              <span className="min-w-0 flex-1">
                <span className="font-medium">{m.name}</span>
                <span className="mt-0.5 block text-xs text-slate-500">
                  {m.failure
                    ? explainScanError(m.failure)
                    : "검사에 실패해 등록되지 않았습니다."}
                </span>
              </span>
              <Badge value="FAILED" />
              <Link
                className="text-brand"
                href={`/new?name=${encodeURIComponent(m.name)}${m.origin_url ? `&url=${encodeURIComponent(m.origin_url)}` : ""}`}
              >
                다시 시도
              </Link>
              <button
                type="button"
                className="text-red-700"
                onClick={async () => {
                  setError("");
                  try {
                    await api.DELETE("/api/repositories/{name}", {
                      params: { path: { name: m.name } },
                    });
                    r.retry();
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                삭제
              </button>
            </li>
          ) : (
            <li key={m.name}>
              <Link
                href={`/@${owner}/${m.name}`}
                className="flex items-center gap-3 py-3 hover:bg-white"
              >
                <span className="min-w-0 flex-1 truncate font-medium">
                  {m.name}
                  {m.version && (
                    <span className="ml-2 text-xs font-normal text-slate-500">
                      {m.version}
                    </span>
                  )}
                </span>
                <span className="text-xs text-slate-500">
                  {m.visibility === "PRIVATE" ? "비공개" : "공개"}
                </span>
                {m.latest_scan && (
                  <>
                    <Badge value={m.latest_scan.status} />
                    <IntegrityBadge info={m.latest_scan.integrity} />
                  </>
                )}
                <span className="text-xs text-slate-500">
                  <Time value={m.created_at} />
                </span>
              </Link>
            </li>
          ),
        )}
      </ul>
    </section>
  );
}

export function Publish() {
  const { user, loginAvailable } = useAuth();
  const path = usePathname();
  const [report, setReport] = useState<Report>();
  // "다시 시도" 링크가 넘겨 준 이름과 주소를 채워 둔다.
  const query = useSearchParams();
  const retry = {
    name: query.get("name") || undefined,
    url: query.get("url") || undefined,
  };
  // 모듈 페이지의 "새 버전 올리기"가 넘겨 준 모듈 이름.
  const repository = query.get("repository") || undefined;
  return (
    <div className="page-enter mx-auto max-w-2xl">
      <h1>{repository ? "새 버전 올리기" : "올리기"}</h1>
      {repository && user && (
        <p className="mt-2 text-slate-600">
          <Link
            className="font-semibold text-brand"
            href={`/@${user.login}/${repository}`}
          >
            @{user.login}/{repository}
          </Link>
          의 다음 버전으로 올립니다. 이름·설명·공개 여부는 그대로입니다.
        </p>
      )}
      {user === undefined ? null : user === null ? (
        <p className="mt-5 text-slate-600">
          모듈을 올리려면 로그인이 필요합니다.{" "}
          {loginAvailable && (
            <a className="font-semibold text-brand" href={loginUrl(path)}>
              GitHub로 로그인
            </a>
          )}
        </p>
      ) : (
        <>
          {report ? (
            <Review
              report={report}
              name={retry.name}
              repository={repository}
              onBack={() => setReport(undefined)}
            />
          ) : (
            <Source key={retry.url} url={retry.url} onReady={setReport} />
          )}
          <MyModules owner={user.login} />
        </>
      )}
    </div>
  );
}
