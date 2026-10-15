import { Suspense } from "react";
import { Publish } from "@/components/publish";
export const metadata = { title: "올리기" };
export default function NewPage() {
  // Publish가 주소의 쿼리(다시 시도 링크)를 읽으므로 Suspense가 필요하다.
  return (
    <Suspense>
      <Publish />
    </Suspense>
  );
}
