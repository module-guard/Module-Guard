import { LegacyScan } from "@/components/legacy-scan";
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <LegacyScan id={id} />;
}
