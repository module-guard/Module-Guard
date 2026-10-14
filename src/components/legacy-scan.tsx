"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { api, unwrap } from "@/api/client";
import { useApi } from "@/api/hooks";
import { State } from "./live-shared";
import { ScanResult } from "./scan-result";

const devMode = process.env.NEXT_PUBLIC_ENABLE_TEST_UI === "true";

/**
 * 옛 주소 /scans/{id}. 모듈에 속한 검사는 모듈의 검사 화면으로 보낸다.
 * 모듈에 속하지 않은 검사(개발용 바로 검사)는 개발 모드에서만 이 자리에서 보여 준다.
 * 쿠키가 있어야 비공개 모듈의 검사를 찾을 수 있어 서버가 아니라 브라우저에서 옮긴다.
 */
export function LegacyScan({ id }: { id: string }) {
  const router = useRouter();
  const r = useApi(
    (signal) =>
      unwrap(
        api.GET("/api/scans/{scan_id}", {
          signal,
          params: { path: { scan_id: id } },
        }),
      ),
    `legacy-scan:${id}`,
  );
  const repository = r.data?.repository;
  useEffect(() => {
    if (repository)
      router.replace(
        `/@${repository.owner}/${repository.name}/scans/${encodeURIComponent(id)}`,
      );
  }, [repository, router, id]);
  if (r.data && !repository && devMode) return <ScanResult id={id} />;
  if (r.error || (r.data && !repository))
    return <State error="검사를 찾을 수 없습니다." />;
  return <State />;
}
