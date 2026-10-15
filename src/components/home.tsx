"use client";
import Link from "next/link";
import { AlertTriangle, Upload } from "lucide-react";
import { api, unwrap } from "@/api/client";
import type { components } from "@/api/generated/schema";
import { useApi } from "@/api/hooks";
import { loginUrl, useAuth } from "./auth";
import { SeverityBars, State, Time } from "./live-shared";
import { FailedCard } from "./profile";
import { RepositoryCard } from "./repository-card";
type Activity = components["schemas"]["Activity"];

const activityText: Record<Activity["kind"], string> = {
  published: "등록됨",
  failed: "검사 실패",
  running: "검사 중",
  forked: "가져옴",
};

export function Home() {
  const { loginAvailable } = useAuth();
  const r = useApi(
    (signal) => unwrap(api.GET("/api/me/home", { signal })),
    "me:home",
  );
  if (r.error && /login|401|로그인/i.test(r.error))
    return (
      <div className="panel mx-auto mt-10 max-w-md p-8 text-center">
        <h1>내 홈</h1>
        <p className="mt-2 text-slate-600">
          로그인하면 내 모듈과 검사 상태를 한곳에서 봅니다.
        </p>
        {loginAvailable && (
          <a
            className="mt-5 inline-flex h-10 items-center rounded-lg bg-brand px-4 font-semibold text-white"
            href={loginUrl("/home")}
          >
            GitHub로 로그인
          </a>
        )}
      </div>
    );
  if (!r.data) return <State error={r.error} retry={r.retry} />;
  const d = r.data;
  const owner = d.repositories[0]?.owner;
  return (
    <div className="page-enter space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1>내 홈</h1>
        <Link
          href="/new"
          className="flex h-9 items-center gap-1.5 rounded-lg bg-brand px-3.5 text-sm font-semibold text-white"
        >
          <Upload size={15} />
          올리기
        </Link>
      </div>

      {d.attention.length > 0 && (
        <section aria-label="확인이 필요한 모듈" className="panel p-5">
          <h2 className="flex items-center gap-2">
            <AlertTriangle size={16} className="text-amber-600" />
            확인이 필요한 모듈
          </h2>
          <ul className="mt-3 space-y-2">
            {d.attention.map((a) => (
              <li key={a.name} className="flex flex-wrap items-center gap-x-3">
                <Link
                  className="font-semibold text-brand"
                  href={owner ? `/@${owner}/${a.name}` : "#"}
                >
                  {a.name}
                </Link>
                <span className="text-slate-600">
                  {a.reason === "failed"
                    ? "검사에 실패해 등록되지 않았습니다."
                    : `마지막 검사가 ${d.stale_after_days}일보다 오래되었습니다.`}
                </span>
                {a.since && (
                  <span className="text-xs text-slate-500">
                    <Time value={a.since} />
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <section aria-label="내 모듈">
          <h2>
            내 모듈{" "}
            <span className="text-sm font-normal text-slate-500">
              {d.repositories.length}
            </span>
          </h2>
          {d.repositories.length === 0 ? (
            <p className="mt-3 text-slate-500">
              아직 모듈이 없습니다.{" "}
              <Link className="text-brand" href="/new">
                올리기
              </Link>
              나{" "}
              <Link className="text-brand" href="/explore">
                탐색
              </Link>
              에서 가져와 보세요.
            </p>
          ) : (
            <div className="mt-3 grid gap-4 md:grid-cols-2">
              {d.repositories.map((card) =>
                card.state === "FAILED" ? (
                  <FailedCard key={card.name} card={card} onChange={r.retry} />
                ) : (
                  <RepositoryCard key={card.name} card={card} />
                ),
              )}
            </div>
          )}
        </section>
        <div className="space-y-6">
          <section aria-label="최근 활동" className="panel p-5">
            <h2>최근 활동</h2>
            {d.activity.length === 0 ? (
              <p className="mt-3 text-slate-500">활동이 없습니다.</p>
            ) : (
              <ol className="mt-3 space-y-2.5">
                {d.activity.map((a, i) => (
                  <li key={i} className="flex items-baseline gap-2">
                    <Link
                      className="min-w-0 truncate font-semibold"
                      href={owner ? `/@${owner}/${a.name}` : "#"}
                    >
                      {a.name}
                    </Link>
                    {a.version && (
                      <span className="text-xs text-slate-500">
                        {a.version}
                      </span>
                    )}
                    <span
                      className={`text-xs ${a.kind === "failed" ? "text-red-700" : "text-slate-600"}`}
                    >
                      {activityText[a.kind]}
                    </span>
                    <span className="ml-auto whitespace-nowrap text-xs text-slate-500">
                      <Time value={a.at} />
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </section>
          <section aria-label="위험도 분포" className="panel p-5">
            <h2>위험도 분포</h2>
            <p className="mt-1 text-xs text-slate-500">
              내 모듈 최신 버전의 본체 탐지 {d.severity.total}건 (예제 제외)
            </p>
            <SeverityBars summary={d.severity} />
          </section>
        </div>
      </div>
    </div>
  );
}
