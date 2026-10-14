import { Suspense } from "react";
import { notFound } from "next/navigation";
import { ModulePage } from "@/components/module-page";
export default async function Page({
  params,
}: {
  params: Promise<{ owner: string; module: string }>;
}) {
  const { owner, module } = await params;
  const login = decodeURIComponent(owner);
  // 모듈 주소는 /@주인/이름 이다. @로 시작하지 않으면 이 화면의 주소가 아니다.
  if (!login.startsWith("@")) notFound();
  return (
    <Suspense>
      <ModulePage owner={login.slice(1)} name={decodeURIComponent(module)} />
    </Suspense>
  );
}
