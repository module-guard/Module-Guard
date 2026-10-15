import { Suspense } from "react";
import { notFound } from "next/navigation";
import { Profile } from "@/components/profile";

// 서버에서 API를 부를 때의 주소. 컨테이너 안에서는 공개 주소와 다를 수 있다.
const API =
  process.env.API_INTERNAL_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  "http://localhost:8000";

export default async function Page({
  params,
}: {
  params: Promise<{ owner: string }>;
}) {
  const login = decodeURIComponent((await params).owner);
  // 프로필 주소는 /@아이디 다.
  if (!login.startsWith("@")) notFound();
  const name = login.slice(1);
  // 사용자가 있는지는 비공개 정보가 아니므로 서버에서 확인해 없으면 HTTP 404로 답한다.
  let missing = false;
  try {
    const response = await fetch(
      `${API}/api/users/${encodeURIComponent(name)}`,
      { cache: "no-store" },
    );
    missing = response.status === 404;
  } catch {
    // API에 닿지 못하면 화면에서 오류를 보여 준다.
  }
  if (missing) notFound();
  return (
    <Suspense>
      <Profile login={name} />
    </Suspense>
  );
}
