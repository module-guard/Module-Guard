"use client";
import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api, unwrap } from "@/api/client";
import { useApi } from "@/api/hooks";
import { Pager, State } from "./live-shared";
import { RepositoryCard } from "./repository-card";
const sorts = [
  ["popular", "인기순"],
  ["recent", "최신순"],
  ["name", "이름순"],
] as const;
type Sort = (typeof sorts)[number][0];

export function Explore() {
  const router = useRouter();
  const pathname = usePathname();
  const query = useSearchParams();
  // 검색어·필터·정렬은 주소에 둔다(공유하고 뒤로 갈 수 있게).
  const search = query.get("q") || "";
  const provider = query.get("provider") || "";
  const sort = (sorts.find(([key]) => key === query.get("sort"))?.[0] ||
    "popular") as Sort;
  const offset = Math.max(0, Number(query.get("offset")) || 0);
  const [text, setText] = useState(search);
  useEffect(() => setText(search), [search]);
  function update(changes: Record<string, string>) {
    const next = new URLSearchParams(query);
    next.delete("offset");
    for (const [key, value] of Object.entries(changes))
      if (value) next.set(key, value);
      else next.delete(key);
    router.replace(next.size ? `${pathname}?${next}` : pathname, {
      scroll: false,
    });
  }
  const providers = useApi(
    (s) => unwrap(api.GET("/api/repository-providers", { signal: s })),
    "providers",
  );
  const r = useApi(
    (s) =>
      unwrap(
        api.GET("/api/repositories", {
          signal: s,
          params: {
            query: {
              limit: 20,
              offset,
              search: search || undefined,
              provider: provider || undefined,
              sort,
            },
          },
        }),
      ),
    `explore:${search}:${provider}:${sort}:${offset}`,
  );
  return (
    <div className="page-enter">
      <h1>탐색</h1>
      <form
        className="mt-5 flex flex-wrap gap-2"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          update({ q: text.trim() });
        }}
      >
        <input
          aria-label="모듈 검색"
          className="input min-w-0 flex-1"
          placeholder="모듈 이름 또는 설명 검색"
          maxLength={256}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <select
          aria-label="Provider"
          className="input !w-auto"
          value={provider}
          onChange={(e) => update({ provider: e.target.value })}
        >
          <option value="">모든 provider</option>
          {(providers.data || []).map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
        <select
          aria-label="정렬"
          className="input !w-auto"
          value={sort}
          onChange={(e) =>
            update({ sort: e.target.value === "popular" ? "" : e.target.value })
          }
        >
          {sorts.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        <button
          type="submit"
          className="rounded-lg bg-brand px-4 font-semibold text-white hover:bg-[#5e4ee6]"
        >
          검색
        </button>
      </form>
      {!r.data ? (
        <State {...r} />
      ) : (
        <>
          <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {r.data.map((card) => (
              <RepositoryCard key={`${card.owner}/${card.name}`} card={card} />
            ))}
          </div>
          {!r.data.length && (
            <p className="mt-5 text-slate-500">
              {search || provider
                ? "조건에 맞는 모듈이 없습니다."
                : "아직 공개된 모듈이 없습니다."}
            </p>
          )}
          <Pager
            offset={offset}
            count={r.data.length}
            setOffset={(value) => {
              const next = new URLSearchParams(query);
              if (value) next.set("offset", String(value));
              else next.delete("offset");
              router.replace(next.size ? `${pathname}?${next}` : pathname);
            }}
          />
        </>
      )}
    </div>
  );
}
