"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, unwrap } from "@/api/client";
import { Button } from "./ui/button";
export function ScanModule() {
  const [source, setSource] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const router = useRouter();
  return (
    <div className="page-enter mx-auto max-w-xl pt-[8vh]">
      <h1 className="text-center">새 검사</h1>
      <form
        className="mt-8"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          try {
            const accepted = await unwrap(
              api.POST("/api/scans", { body: { source } }),
            );
            router.push(`/scans/${accepted.scan_id}`);
          } catch (e) {
            setError((e as Error).message);
            setBusy(false);
          }
        }}
      >
        <label className="label" htmlFor="repository">
          GitHub 저장소 주소
        </label>
        <div className="flex gap-2">
          <input
            id="repository"
            className="input"
            type="url"
            required
            placeholder="https://github.com/소유자/저장소 (공개 저장소)"
            value={source}
            onChange={(e) => setSource(e.target.value)}
          />
          <Button disabled={busy} type="submit" className="h-auto shrink-0">
            {busy ? "요청 중…" : "검사 시작"}
          </Button>
        </div>
        {error && (
          <p role="alert" className="mt-3 text-red-700">
            {error}
          </p>
        )}
        <ul className="mt-4 flex flex-wrap gap-2" aria-label="분석 범위">
          {["보안 설정", "공급망 신뢰", "상관 위험"].map((x) => (
            <li
              key={x}
              className="rounded-full border border-line bg-white px-3 py-1 text-xs text-slate-500"
            >
              {x}
            </li>
          ))}
        </ul>
      </form>
    </div>
  );
}
