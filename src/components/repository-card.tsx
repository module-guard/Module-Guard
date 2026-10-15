import Link from "next/link";
import { GitFork, Lock, Star } from "lucide-react";
import type { components } from "@/api/generated/schema";
import { Badge, Time } from "./live-shared";
import { SecurityBadge } from "./security-badge";
export type Card = components["schemas"]["RepositoryCard"];

/** 탐색과 프로필이 같이 쓰는 모듈 카드. 카드 전체가 모듈 페이지로 가는 링크다. */
export function RepositoryCard({ card }: { card: Card }) {
  return (
    <Link
      href={`/@${card.owner}/${card.name}`}
      className="panel flex flex-col gap-3 p-5 hover:border-brand/40"
    >
      <div className="min-w-0">
        {/* 이름이 먼저, 한 줄로. 주인 이름이 길어도 모듈 이름이 잘리지 않게 따로 둔다. */}
        <h2 className="flex items-center gap-2 font-semibold">
          <span className="truncate" title={card.name}>
            {card.name}
          </span>
          {card.visibility === "PRIVATE" && (
            <Lock
              size={13}
              className="shrink-0 text-slate-400"
              aria-label="비공개"
            />
          )}
        </h2>
        <p className="truncate text-xs text-slate-500" title={`@${card.owner}`}>
          @{card.owner}
        </p>
        <p className="mt-1 truncate text-slate-600">
          {card.description || " "}
        </p>
      </div>
      {card.state === "PUBLISHED" && card.badge ? (
        <SecurityBadge
          criticalRules={card.badge.critical_rules}
          highRules={card.badge.high_rules}
          unknown={card.badge.unknown}
          version={card.badge.version}
          scannedAt={card.badge.scanned_at}
        />
      ) : (
        <span>
          <Badge value={card.state === "FAILED" ? "FAILED" : "RUNNING"} />
        </span>
      )}
      <p className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
        {card.mirror && (
          <span className="rounded bg-slate-100 px-1.5 py-0.5 font-semibold text-slate-700">
            미러
          </span>
        )}
        {card.provider && <span>{card.provider}</span>}
        <span className="inline-flex items-center gap-1" title="별표">
          <Star size={13} />
          <span className="tabular-nums">{card.star_count}</span>
        </span>
        <span className="inline-flex items-center gap-1" title="가져간 수">
          <GitFork size={13} />
          <span className="tabular-nums">{card.fork_count}</span>
        </span>
        {card.forked_from && (
          <span className="truncate">
            원본: @{card.forked_from.owner}/{card.forked_from.name}
          </span>
        )}
        <span className="ml-auto">
          <Time value={card.updated_at} />
        </span>
      </p>
    </Link>
  );
}
