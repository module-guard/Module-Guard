"use client";
import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, unwrap } from "@/api/client";
import { explainScanError } from "@/api/errors";
import { useApi } from "@/api/hooks";
import { Pager, State } from "./live-shared";
import { RepositoryCard, type Card } from "./repository-card";
const tabs = [
  ["owned", "모듈", "repository_count"],
  ["forked", "가져온 모듈", "fork_count"],
  ["starred", "별표", "star_count"],
] as const;
type Kind = (typeof tabs)[number][0];

/** 실패한 올리기는 주인에게만 보인다: 다시 올리거나 지울 수 있다. */
export function FailedCard({ card, onChange }: { card: Card; onChange: () => void }) {
  const [error, setError] = useState("");
  return (
    <div className="panel flex flex-col gap-2 p-5">
      <h2 className="truncate">{card.name}</h2>
      <p className="text-xs text-slate-500">
        {card.failure
          ? explainScanError(card.failure)
          : "검사에 실패해 등록되지 않았습니다."}
      </p>
      {error && (
        <p role="alert" className="text-xs text-red-700">
          {error}
        </p>
      )}
      <p className="mt-auto flex gap-4">
        <Link
          className="text-brand"
          href={`/new?name=${encodeURIComponent(card.name)}`}
        >
          다시 시도
        </Link>
        <button
          type="button"
          className="text-red-700"
          onClick={async () => {
            try {
              await api.DELETE("/api/repositories/{name}", {
                params: { path: { name: card.name } },
              });
              onChange();
            } catch (e) {
              setError((e as Error).message);
            }
          }}
        >
          삭제
        </button>
      </p>
    </div>
  );
}

export function Profile({ login }: { login: string }) {
  const query = useSearchParams();
  const kind = (tabs.find(([key]) => key === query.get("tab"))?.[0] ||
    "owned") as Kind;
  const [offset, setOffset] = useState(0);
  const profile = useApi(
    (s) =>
      unwrap(
        api.GET("/api/users/{login}", {
          signal: s,
          params: { path: { login } },
        }),
      ),
    `user:${login}`,
  );
  const list = useApi(
    (s) =>
      unwrap(
        api.GET("/api/users/{login}/repositories", {
          signal: s,
          params: { path: { login }, query: { kind, limit: 20, offset } },
        }),
      ),
    `user:${login}:${kind}:${offset}`,
  );
  if (!profile.data)
    return (
      <State
        error={
          profile.error && /not found/i.test(profile.error)
            ? "사용자를 찾을 수 없습니다."
            : profile.error
        }
        retry={profile.retry}
      />
    );
  const user = profile.data;
  return (
    <div className="page-enter">
      <div className="flex items-center gap-4">
        {user.avatar_url ? (
          <Image
            src={user.avatar_url}
            alt=""
            width={64}
            height={64}
            className="h-16 w-16 rounded-full"
          />
        ) : (
          <span className="flex h-16 w-16 items-center justify-center rounded-full bg-[#eeebff] text-xl font-bold uppercase text-brand">
            {user.login.slice(0, 2)}
          </span>
        )}
        <div className="min-w-0">
          <h1 className="truncate">{user.name || user.login}</h1>
          <p className="text-slate-500">@{user.login}</p>
        </div>
      </div>
      {user.mirror && (
        <p className="mt-4 rounded bg-slate-100 px-4 py-3 text-slate-600">
          <strong className="text-ink">미러 계정</strong>입니다. 공개 원본
          저장소의 특정 릴리스를 그대로 옮겨 검사해 둔 것으로, ModuleGuard가
          운영하며 원 저작자와는 무관합니다. 각 모듈 페이지에 원본 주소와
          라이선스가 있습니다.
        </p>
      )}
      <nav
        className="mb-6 mt-6 flex gap-5 overflow-x-auto border-b border-line"
        aria-label="프로필 탭"
      >
        {tabs.map(([key, label, count]) => (
          <Link
            key={key}
            href={`/@${user.login}${key === "owned" ? "" : `?tab=${key}`}`}
            scroll={false}
            onClick={() => setOffset(0)}
            aria-current={kind === key ? "page" : undefined}
            className={`whitespace-nowrap border-b-2 px-1 py-3 ${kind === key ? "border-brand font-semibold text-brand" : "border-transparent text-slate-500"}`}
          >
            {label}
            <span className="ml-1.5 text-xs font-normal tabular-nums text-slate-400">
              {user[count]}
            </span>
          </Link>
        ))}
      </nav>
      {!list.data ? (
        <State {...list} />
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {list.data.map((card) =>
              card.state === "FAILED" && user.is_self ? (
                <FailedCard
                  key={card.name}
                  card={card}
                  onChange={() => {
                    list.retry();
                    profile.retry();
                  }}
                />
              ) : (
                <RepositoryCard
                  key={`${card.owner}/${card.name}`}
                  card={card}
                />
              ),
            )}
          </div>
          {!list.data.length && (
            <p className="text-slate-500">
              {kind === "owned"
                ? "아직 올린 모듈이 없습니다."
                : kind === "forked"
                  ? "가져온 모듈이 없습니다."
                  : "별표한 모듈이 없습니다."}
            </p>
          )}
          <Pager
            offset={offset}
            count={list.data.length}
            setOffset={setOffset}
          />
        </>
      )}
    </div>
  );
}
