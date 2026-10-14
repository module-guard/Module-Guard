import { Suspense } from "react";
import { ScanModule } from "@/components/scan-module";
import { PageLoading } from "@/components/page-loading";
export const metadata = { title: "새 검사" };
export default function ScanPage() {
  return (
    <Suspense fallback={<PageLoading />}>
      <ScanModule />
    </Suspense>
  );
}
