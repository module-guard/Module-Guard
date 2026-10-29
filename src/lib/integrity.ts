/** The expected digest comes from the persisted artifact preparation response. */
export async function verifyDownload(
  blob: Blob,
  expected: string,
): Promise<void> {
  if (!/^[0-9a-f]{64}$/.test(expected) || !globalThis.crypto?.subtle) {
    throw new Error(
      "무결성 검증 실패 — 안전한 연결과 Web Crypto 지원이 필요합니다.",
    );
  }
  const bytes = await blob.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const actual = Array.from(new Uint8Array(digest), (b) =>
    b.toString(16).padStart(2, "0"),
  ).join("");
  if (actual !== expected) throw new Error("파일 변조 의심 — 다운로드 차단");
}

export async function saveVerifiedDownload(
  blob: Blob,
  expected: string,
  filename: string,
) {
  await verifyDownload(blob, expected);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  try {
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
  } finally {
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
