import { Suspense } from "react";
import { Explore } from "@/components/explore";
export const metadata = { title: "탐색" };
export default function ExplorePage() {
  return (
    <Suspense>
      <Explore />
    </Suspense>
  );
}
