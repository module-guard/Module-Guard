import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError } from "@/api/client";
import { scanFixture } from "../../../e2e/fixtures/analysis-coverage";
import {
  AttemptLinks,
  attemptHref,
  expired,
  explainRetryError,
} from "../scan-retry";

const repository = {
  owner: "dev-user",
  name: "net",
  version: "v1",
  visibility: "PUBLIC" as const,
};
const OLD = "11111111-1111-4111-8111-111111111111";
const NEW = "33333333-3333-4333-8333-333333333333";

describe("retry errors", () => {
  it("sends an expired or missing prepared source back to the source step", () => {
    for (const error of [
      new ApiError(
        404,
        "준비된 업로드가 없습니다.",
        "StagedUploadNotFoundError",
      ),
      new ApiError(410, "gone", "PreparedSourceMissingError"),
    ]) {
      const problem = explainRetryError(error);
      expect(problem.next).toBe("source");
      expect(problem.message).toContain("다시 올려");
    }
  });

  it("asks for the original source on a mismatch", () => {
    const problem = explainRetryError(new ApiError(422, "x", "SourceMismatch"));
    expect(problem.next).toBe("source");
    expect(problem.message).toContain("원래 등록한 출처");
  });

  it("points a changed source to a new version", () => {
    const problem = explainRetryError(new ApiError(422, "x", "SourceChanged"));
    expect(problem).toMatchObject({ next: "stop", newVersion: true });
  });

  it("keeps the prepared source on a full queue or rate limit", () => {
    expect(
      explainRetryError(new ApiError(503, "x", "ScanQueueFullError")).next,
    ).toBe("review");
    expect(
      explainRetryError(new ApiError(429, "x", "RateLimitedError")).next,
    ).toBe("review");
  });

  it("separates conflicts, login and access errors", () => {
    expect(
      explainRetryError(
        new ApiError(409, "이미 다시 시도된 검사입니다.", "ConflictError"),
      ),
    ).toMatchObject({
      next: "conflict",
      message: "이미 다시 시도된 검사입니다.",
    });
    expect(
      explainRetryError(new ApiError(401, "x", "AuthenticationRequiredError")),
    ).toMatchObject({ next: "stop", login: true });
    expect(
      explainRetryError(new ApiError(403, "x", "ForbiddenOriginError")).next,
    ).toBe("stop");
    const notFound = explainRetryError(
      new ApiError(404, "Scan not found.", "NotFoundError"),
    );
    expect(notFound.next).toBe("stop");
    expect(notFound.message).not.toContain("Scan not found");
  });

  it("does not echo an unexpected server message", () => {
    const problem = explainRetryError(
      new ApiError(500, "Traceback SECRET_STDERR", "SnapshotError"),
    );
    expect(problem.message).not.toContain("SECRET");
  });
});

describe("attempt links", () => {
  it("links to the previous and next attempts in the same repository", () => {
    const html = renderToStaticMarkup(
      <AttemptLinks
        scan={scanFixture({ repository, retry_of: OLD, retried_by: NEW })}
      />,
    );
    expect(html).toContain(`href="/@dev-user/net/scans/${OLD}"`);
    expect(html).toContain(`href="/@dev-user/net/scans/${NEW}"`);
    expect(html).toContain("이전 시도");
    expect(html).toContain("다음 시도");
  });

  it("renders nothing without a linked attempt or repository", () => {
    expect(
      renderToStaticMarkup(<AttemptLinks scan={scanFixture({ repository })} />),
    ).toBe("");
    expect(
      renderToStaticMarkup(
        <AttemptLinks scan={scanFixture({ retry_of: OLD })} />,
      ),
    ).toBe("");
  });

  it("builds attempt urls and detects an expired source", () => {
    expect(attemptHref(repository, NEW)).toBe(`/@dev-user/net/scans/${NEW}`);
    const report = { expires_at: "2026-10-29T00:00:00Z" } as Parameters<
      typeof expired
    >[0];
    expect(expired(report, Date.parse("2026-10-29T00:00:01Z"))).toBe(true);
    expect(expired(report, Date.parse("2026-10-28T23:59:59Z"))).toBe(false);
  });
});
