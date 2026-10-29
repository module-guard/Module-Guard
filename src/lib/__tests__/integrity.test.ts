import { describe, expect, it, vi, afterEach } from "vitest";
import { createHash } from "node:crypto";
import { saveVerifiedDownload, verifyDownload } from "../integrity";

afterEach(() => vi.unstubAllGlobals());
describe("download integrity", () => {
  it("hashes the received bytes including binary bytes", async () => {
    const bytes = new Uint8Array([0, 255, 1, 7]);
    await expect(
      verifyDownload(
        new Blob([bytes]),
        createHash("sha256").update(bytes).digest("hex"),
      ),
    ).resolves.toBeUndefined();
  });
  it("refuses malformed or mismatching digests before saving", async () => {
    const document = { createElement: vi.fn() };
    vi.stubGlobal("document", document);
    await expect(
      saveVerifiedDownload(
        new Blob(["tampered"]),
        "a".repeat(64),
        "module.zip",
      ),
    ).rejects.toThrow("다운로드 차단");
    await expect(
      saveVerifiedDownload(new Blob(["data"]), "", "module.zip"),
    ).rejects.toThrow("검증 실패");
    expect(document.createElement).not.toHaveBeenCalled();
  });
  it("only invokes file saving after the received bytes match", async () => {
    const link = { click: vi.fn(), remove: vi.fn(), href: "", download: "" };
    vi.stubGlobal("document", {
      createElement: vi.fn(() => link),
      body: { appendChild: vi.fn() },
    });
    await saveVerifiedDownload(
      new Blob(["abc"]),
      createHash("sha256").update("abc").digest("hex"),
      "module.zip",
    );
    expect(link.click).toHaveBeenCalledOnce();
    expect(link.download).toBe("module.zip");
  });
  it("fails closed without Web Crypto", async () => {
    vi.stubGlobal("crypto", {});
    await expect(
      verifyDownload(new Blob(["abc"]), "a".repeat(64)),
    ).rejects.toThrow("검증 실패");
  });
});
