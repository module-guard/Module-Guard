import { notFound } from "next/navigation";
import { DevelopmentTest } from "@/components/development-test";
export default function Page() {
  if (process.env.NEXT_PUBLIC_ENABLE_TEST_UI !== "true") notFound();
  return <DevelopmentTest />;
}
