import Link from "next/link";
import { ShieldQuestion } from "lucide-react";
import { Button } from "@/components/ui/button";
export default function NotFound() {
  return (
    <div className="panel flex min-h-[50vh] flex-col items-center justify-center gap-4 text-center">
      <ShieldQuestion size={40} className="text-brand" />
      <h1 className="text-xl font-semibold">페이지를 찾을 수 없습니다</h1>
      <p className="text-sm text-slate-500">
        주소가 바뀌었거나 없는 페이지입니다.
      </p>
      <Button asChild>
        <Link href="/explore">모듈 탐색</Link>
      </Button>
    </div>
  );
}
