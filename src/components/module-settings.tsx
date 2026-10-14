"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/api/client";
import type { components } from "@/api/generated/schema";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from "./ui/dialog";
type Detail = components["schemas"]["RepositoryDetail"];
type Visibility = components["schemas"]["Visibility"];
const NAME = /^[a-z0-9][a-z0-9_-]{0,99}$/;
const message = (e: unknown, fallback: string) =>
  e instanceof ApiError ? e.message : fallback;

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className="rounded-lg border border-line bg-white p-5"
    >
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Basics({ detail, reload }: { detail: Detail; reload: () => void }) {
  const router = useRouter();
  const [name, setName] = useState(detail.name);
  const [description, setDescription] = useState(detail.description || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const renamed = name !== detail.name;
  const changed = renamed || description !== (detail.description || "");
  return (
    <Section title="이름과 설명">
      <form
        className="mt-4 space-y-4"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          try {
            await api.PATCH("/api/repositories/{name}", {
              params: { path: { name: detail.name } },
              body: { ...(renamed ? { name } : {}), description },
            });
            if (renamed)
              router.replace(`/@${detail.owner}/${name}?tab=settings`);
            // 다시 불러오면 위쪽 제목 아래 설명이 바뀐 것으로 저장을 확인한다.
            else reload();
          } catch (e) {
            setError(message(e, "저장하지 못했습니다."));
          } finally {
            setBusy(false);
          }
        }}
      >
        <div>
          <label className="label" htmlFor="settings-name">
            이름
          </label>
          <input
            id="settings-name"
            className="input"
            required
            value={name}
            aria-describedby="settings-name-help"
            onChange={(e) => setName(e.target.value.toLowerCase())}
          />
          <p id="settings-name-help" className="mt-1 text-xs text-slate-500">
            {renamed
              ? `주소가 /@${detail.owner}/${NAME.test(name) ? name : "…"}(으)로 바뀌고, 이전 주소는 더 이상 열리지 않습니다.`
              : "영문 소문자, 숫자, -, _ 만 쓸 수 있습니다."}
          </p>
        </div>
        <div>
          <label className="label" htmlFor="settings-description">
            설명
          </label>
          <input
            id="settings-description"
            className="input"
            maxLength={500}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        {error && (
          <p role="alert" className="text-red-700">
            {error}
          </p>
        )}
        <Button type="submit" disabled={busy || !changed || !NAME.test(name)}>
          {busy ? "저장 중…" : "저장"}
        </Button>
      </form>
    </Section>
  );
}

function VisibilitySetting({
  detail,
  reload,
}: {
  detail: Detail;
  reload: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const isPublic = detail.visibility === "PUBLIC";
  const next: Visibility = isPublic ? "PRIVATE" : "PUBLIC";
  return (
    <Section title="공개 여부">
      <p className="mt-2 text-slate-600">
        지금은 <strong>{isPublic ? "공개" : "비공개"}</strong>입니다.{" "}
        {isPublic
          ? "누구나 탐색에서 찾고 볼 수 있습니다."
          : "나만 볼 수 있고 탐색과 프로필에 나오지 않습니다."}
      </p>
      {isPublic && detail.fork_count > 0 && (
        <p className="mt-1 text-xs text-slate-500">
          비공개로 바꿔도 이미 가져간 {detail.fork_count}개의 사본은 그대로
          남습니다.
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-red-700">
          {error}
        </p>
      )}
      <Button
        type="button"
        variant="outline"
        className="mt-4"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            await api.PATCH("/api/repositories/{name}", {
              params: { path: { name: detail.name } },
              body: { visibility: next },
            });
            reload();
          } catch (e) {
            setError(message(e, "바꾸지 못했습니다."));
          } finally {
            setBusy(false);
          }
        }}
      >
        {isPublic ? "비공개로 바꾸기" : "공개로 바꾸기"}
      </Button>
    </Section>
  );
}

function Delete({ detail }: { detail: Detail }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <section
      aria-label="모듈 삭제"
      className="rounded-lg border border-red-200 bg-white p-5"
    >
      <h2 className="text-base font-semibold text-red-800">모듈 삭제</h2>
      <p className="mt-2 text-slate-600">
        모든 버전과 검사 결과, 별표가 함께 지워지며 되돌릴 수 없습니다. 다른
        사람이 가져간 사본은 그대로 남습니다.
      </p>
      <Dialog
        open={open}
        onOpenChange={(value) => {
          setOpen(value);
          setTyped("");
          setError("");
        }}
      >
        <DialogTrigger asChild>
          <Button type="button" variant="outline" className="mt-4 text-red-700">
            이 모듈 삭제
          </Button>
        </DialogTrigger>
        <DialogContent>
          <DialogTitle className="text-lg font-semibold">
            @{detail.owner}/{detail.name} 삭제
          </DialogTitle>
          <DialogDescription className="mt-1 text-slate-600">
            되돌릴 수 없습니다. 계속하려면 모듈 이름{" "}
            <strong className="break-all text-ink">{detail.name}</strong>을(를)
            그대로 입력하세요.
          </DialogDescription>
          <form
            className="mt-4 space-y-4"
            onSubmit={async (e) => {
              e.preventDefault();
              if (typed !== detail.name) return;
              setBusy(true);
              setError("");
              try {
                await api.DELETE("/api/repositories/{name}", {
                  params: {
                    path: { name: detail.name },
                    query: { confirm: typed },
                  },
                });
                router.push(`/@${detail.owner}`);
              } catch (e) {
                setError(message(e, "삭제하지 못했습니다."));
                setBusy(false);
              }
            }}
          >
            <div>
              <label className="label" htmlFor="delete-confirm">
                모듈 이름 확인
              </label>
              <input
                id="delete-confirm"
                className="input"
                autoComplete="off"
                spellCheck={false}
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
              />
            </div>
            {error && (
              <p role="alert" className="text-red-700">
                {error}
              </p>
            )}
            <Button
              type="submit"
              className="bg-red-700 hover:bg-red-800"
              disabled={busy || typed !== detail.name}
            >
              {busy ? "삭제 중…" : "영구 삭제"}
            </Button>
          </form>
        </DialogContent>
      </Dialog>
    </section>
  );
}

export function ModuleSettings({
  detail,
  reload,
}: {
  detail: Detail;
  reload: () => void;
}) {
  return (
    <div className="max-w-2xl space-y-5">
      <Basics
        key={`${detail.name}:${detail.description}`}
        detail={detail}
        reload={reload}
      />
      <VisibilitySetting detail={detail} reload={reload} />
      <Delete detail={detail} />
    </div>
  );
}
