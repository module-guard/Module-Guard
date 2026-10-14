import { notFound } from "next/navigation";
import { ScanResult } from "@/components/scan-result";
export default async function Page({
  params,
}: {
  params: Promise<{ owner: string; id: string }>;
}) {
  const { owner, id } = await params;
  if (!decodeURIComponent(owner).startsWith("@")) notFound();
  return <ScanResult id={id} />;
}
