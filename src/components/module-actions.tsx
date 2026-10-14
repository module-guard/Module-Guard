"use client";
import { useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { GitFork, Star } from "lucide-react";
import { api, ApiError, unwrap } from "@/api/client";
import type { components } from "@/api/generated/schema";
import { loginUrl, useAuth } from "./auth";
import { SecurityBadge } from "./security-badge";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "./ui/dialog";
type Detail = components["schemas"]["RepositoryDetail"];
const NAME = /^[a-z0-9][a-z0-9_-]{0,99}$/;
const action =
  "flex h-9 items-center gap-1.5 whitespace-nowrap rounded-lg border border-line bg-white px-3.5 text-sm font-semibold hover:bg-slate-50";

export function StarButton({ detail }: { detail: Detail }) {
  const { user, loginAvailable } = useAuth();
  const path = usePathname();
  const [state, setState] = useState({
    starred: detail.starred,
    count: detail.star_count,
  });
  const [busy, setBusy] = useState(false);
  const label = state.starred ? "별표 취소" : "별표";
  const body = (
    <>
      <Star
        size={15}
        className={state.starred ? "fill-amber-400 text-amber-400" : ""}
      />
      {label}
      <span className="tabular-nums text-slate-500">{state.count}</span>
    </>
  );
  // 로그아웃 상태에서는 누르면 로그인하러 간다.
  if (!user)
    return loginAvailable ? (
      <a className={action} href={loginUrl(path)}>
        {body}
      </a>
    ) : (
      <span className={`${action} cursor-default`}>{body}</span>
    );
  return (
    <button
      type="button"
      className={action}
      aria-pressed={state.starred}
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          const params = { path: { owner: detail.owner, name: detail.name } };
          const next = await unwrap(
            state.starred
              ? api.DELETE("/api/repositories/{owner}/{name}/star", { params })
              : api.PUT("/api/repositories/{owner}/{name}/star", { params }),
          );
          setState({ starred: next.starred, count: next.star_count });
        } finally {
          setBusy(false);
        }
      }}
    >
      {body}
    </button>
  );
}

export function ForkButton({ detail }: { detail: Detail }) {
  const { user, loginAvailable } = useAuth();
  const path = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(detail.name);
  const [visibility, setVisibility] = useState<"PRIVATE" | "PUBLIC">("PRIVATE");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const label = (
    <>
      <GitFork size={15} />내 저장소로 가져오기
      <span className="tabular-nums text-slate-500">{detail.fork_count}</span>
    </>
  );
  // 자기 모듈은 가져올 수 없다. 저장된 버전이 없으면 가져올 것이 없다.
  if (detail.is_owner || !detail.latest_version) return null;
  if (!user)
    return loginAvailable ? (
      <a className={action} href={loginUrl(path)}>
        {label}
      </a>
    ) : null;
  const version = detail.version;
  const scan = version?.scan;
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button type="button" className={action}>
          {label}
        </button>
      </DialogTrigger>
      <DialogContent
        // 처음 초점은 이름 입력란에 둔다(배지에 가면 설명 풍선이 창을 가린다).
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          document.getElementById("fork-name")?.focus();
        }}
      >
        <DialogTitle className="text-lg font-semibold">
          내 저장소로 가져오기
        </DialogTitle>
        <DialogDescription className="mt-1 text-slate-600">
          @{detail.owner}/{detail.name} {detail.latest_version}을 @{user.login}{" "}
          아래로 가져옵니다. 파일은 그대로이고 검사 결과도 함께 옵니다.
        </DialogDescription>
        {/* 가져오기 전에 원본의 보안 상태와 라이선스를 확인하게 한다. */}
        <section
          aria-label="원본 보안 요약"
          className="mt-4 rounded-lg border border-line bg-[#fafbfe] p-4"
        >
          {version && scan ? (
            <>
              <SecurityBadge
                criticalRules={version.critical_rules}
                highRules={version.high_rules}
                unknown={scan.summary.unknown}
                version={version.version}
                scannedAt={scan.finished_at || scan.created_at}
              />
              <p className="mt-3 text-xs text-slate-600">
                탐지 {scan.summary.total}건 · Critical {scan.summary.critical} ·
                High {scan.summary.high} · Medium {scan.summary.medium}
                {scan.scope_summary?.example?.total
                  ? ` · 예제에서 ${scan.scope_summary.example.total}건`
                  : ""}
              </p>
            </>
          ) : (
            <p className="text-xs text-slate-600">검사 결과가 없습니다.</p>
          )}
          <p className="mt-1 text-xs text-slate-600">
            라이선스: {version?.license || "표시 없음"}
          </p>
        </section>
        <form
          className="mt-4 space-y-4"
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              const created = await unwrap(
                api.POST("/api/repositories/{owner}/{name}/fork", {
                  params: {
                    path: { owner: detail.owner, name: detail.name },
                  },
                  body: { name, visibility },
                }),
              );
              router.push(`/@${created.owner}/${created.name}`);
            } catch (e) {
              setError(
                e instanceof ApiError ? e.message : "가져오지 못했습니다.",
              );
              setBusy(false);
            }
          }}
        >
          <div>
            <label className="label" htmlFor="fork-name">
              이름
            </label>
            <input
              id="fork-name"
              className="input"
              required
              value={name}
              onChange={(e) => setName(e.target.value.toLowerCase())}
            />
          </div>
          <fieldset>
            <legend className="label">공개 여부</legend>
            <div className="flex gap-5">
              {(
                [
                  ["PRIVATE", "비공개"],
                  ["PUBLIC", "공개"],
                ] as const
              ).map(([value, text]) => (
                <label key={value} className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="fork-visibility"
                    checked={visibility === value}
                    onChange={() => setVisibility(value)}
                  />
                  {text}
                </label>
              ))}
            </div>
          </fieldset>
          {error && (
            <p role="alert" className="text-red-700">
              {error}
            </p>
          )}
          <Button type="submit" disabled={busy || !NAME.test(name)}>
            {busy ? "가져오는 중…" : "가져오기"}
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
