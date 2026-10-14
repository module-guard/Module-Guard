"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { AuthProvider } from "./auth";
import { TopNav } from "./top-nav";
const loginErrors: Record<string, string> = {
  state: "로그인 요청이 만료되었거나 올바르지 않습니다. 다시 시도해 주세요.",
  denied: "GitHub에서 로그인을 취소했습니다.",
  github:
    "GitHub에서 계정 정보를 가져오지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
};
function LoginError() {
  const [message, setMessage] = useState("");
  useEffect(() => {
    const url = new URL(window.location.href);
    const code = url.searchParams.get("login_error");
    if (!code) return;
    setMessage(loginErrors[code] || "로그인하지 못했습니다.");
    // 새로고침해도 다시 뜨지 않게 주소에서 지운다.
    url.searchParams.delete("login_error");
    window.history.replaceState(null, "", url);
  }, []);
  return message ? (
    <p role="alert" className="mb-5 rounded bg-red-50 px-4 py-3 text-red-700">
      {message}
    </p>
  ) : null;
}
export function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  // 모듈의 검사 화면(/@주인/이름/scans/…)에서는 그 모듈로 돌아간다.
  const moduleScan = decodeURIComponent(path).match(
    /^(\/@[^/]+\/[^/]+)\/scans\//,
  );
  const back = moduleScan ? { href: moduleScan[1], label: "모듈" } : null;
  return (
    <AuthProvider>
      <div className="min-h-screen">
        <TopNav />
        <main className="mx-auto max-w-[1280px] px-4 py-7 sm:px-8 sm:py-8">
          <LoginError />
          {back && (
            <Link
              href={back.href}
              className="mb-4 inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-navy"
            >
              <ArrowLeft size={15} />
              {back.label}
            </Link>
          )}
          {children}
        </main>
      </div>
    </AuthProvider>
  );
}
