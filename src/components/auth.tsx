"use client";
import { createContext, useCallback, useContext } from "react";
import { api, apiBaseUrl, unwrap } from "@/api/client";
import type { components } from "@/api/generated/schema";
import { useApi } from "@/api/hooks";
export type Me = components["schemas"]["UserResponse"];
type Auth = {
  /** undefined: 아직 확인 중, null: 로그아웃 상태 */
  user: Me | null | undefined;
  /** 서버에 GitHub 로그인 설정이 있는지. 없으면 로그인 버튼을 숨긴다. */
  loginAvailable: boolean;
  logout: () => Promise<void>;
};
const AuthContext = createContext<Auth>({
  user: undefined,
  loginAvailable: false,
  logout: async () => {},
});
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const r = useApi((s) => unwrap(api.GET("/api/me", { signal: s })), "me");
  const { retry } = r;
  const logout = useCallback(async () => {
    await api.POST("/api/auth/logout");
    retry();
  }, [retry]);
  // API에 닿지 못하면 로그아웃 상태로 본다(상태 점이 연결 문제를 따로 알린다).
  const user = r.data ? r.data.user : r.error ? null : undefined;
  return (
    <AuthContext.Provider
      value={{ user, loginAvailable: r.data?.login_available ?? false, logout }}
    >
      {children}
    </AuthContext.Provider>
  );
}
export function useAuth() {
  return useContext(AuthContext);
}
/** 로그인 뒤 돌아올 내부 경로를 붙인 로그인 주소. 검증은 서버가 한다. */
export function loginUrl(next: string) {
  return `${apiBaseUrl}/api/auth/github/login?next=${encodeURIComponent(next)}`;
}
