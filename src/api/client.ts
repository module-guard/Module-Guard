import createClient from "openapi-fetch";
import type { paths } from "./generated/schema";
export type ApiErrorDetail = {
  path?: string | null;
  line?: number;
  reason?: string;
  rule?: string;
  preview?: string | null;
};
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
    public details: ApiErrorDetail[] = [],
  ) {
    super(message);
  }
}
export const apiBaseUrl =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
function fetchWithTimeout(timeout: number) {
  return async (request: Request) => {
    const controller = new AbortController();
    const abort = () => controller.abort();
    request.signal.addEventListener("abort", abort, { once: true });
    if (request.signal.aborted) controller.abort();
    const timer = setTimeout(abort, timeout);
    try {
      const response = await fetch(
        new Request(request, {
          signal: controller.signal,
          cache: "no-store",
          // 세션 쿠키(HttpOnly)를 API로 같이 보낸다.
          credentials: "include",
        }),
      );
      if (!response.ok) {
        const body = await response
          .clone()
          .json()
          .catch(() => null);
        throw new ApiError(
          response.status,
          body?.error?.message ||
            (response.status === 422
              ? "입력값을 확인해 주세요."
              : `API 오류 (${response.status})`),
          body?.error?.code,
          body?.error?.details,
        );
      }
      return response;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(
        0,
        controller.signal.aborted
          ? "요청이 취소되었거나 응답 시간이 초과되었습니다."
          : "백엔드에 연결할 수 없습니다. 서버 실행 상태를 확인해 주세요.",
      );
    } finally {
      clearTimeout(timer);
      request.signal.removeEventListener("abort", abort);
    }
  };
}
export const api = createClient<paths>({
  baseUrl: apiBaseUrl,
  fetch: fetchWithTimeout(30000),
});
/** 올리기 준비처럼 오래 걸리는 요청용(GitHub clone 제한이 120초). */
export const slowApi = createClient<paths>({
  baseUrl: apiBaseUrl,
  fetch: fetchWithTimeout(150000),
});
export async function unwrap<T>(
  response: Promise<{ data?: T; error?: unknown }>,
): Promise<T> {
  const result = await response;
  if (result.data === undefined)
    throw new ApiError(0, "API 응답이 비어 있습니다.");
  return result.data;
}
