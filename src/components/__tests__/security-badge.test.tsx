import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { FailedCard } from "../profile";
import { SecurityBadge } from "../security-badge";
import { RepositoryCard, type Card } from "../repository-card";

describe("security badge completeness", () => {
  it.each([
    ["COMPLETED", false, 0, 0, "주요 위험 없음"],
    ["COMPLETED", true, 0, 0, "부분 분석"],
    ["COMPLETED", true, 2, 0, "위험 2종 · 부분 분석"],
    ["COMPLETED", true, 0, 3, "주의 3종 · 부분 분석"],
    ["COMPLETED", false, 2, 0, "위험 2종"],
    ["FAILED", false, 0, 0, "분석 실패"],
    ["FAILED", true, 2, 0, "위험 2종 · 분석 실패 · 부분 분석"],
    [null, false, 0, 0, "미검사"],
    [undefined, false, 0, 0, "미검사"],
    ["QUEUED", false, 0, 0, "분석 중"],
    ["COLLECTING", false, 0, 0, "분석 중"],
    ["SCANNING", false, 0, 0, "분석 중"],
    ["NORMALIZING", false, 0, 0, "분석 중"],
  ] as const)(
    "%s partial=%s critical=%s high=%s",
    (scanStatus, partialResult, criticalRules, highRules, label) => {
      const html = renderToStaticMarkup(
        <SecurityBadge
          {...{ scanStatus, partialResult, criticalRules, highRules }}
        />,
      );
      expect(html).toContain(label);
      if (label !== "주요 위험 없음")
        expect(html).not.toContain("주요 위험 없음");
      if (criticalRules) expect(html).toContain("text-red-700");
      if (highRules) expect(html).toContain("text-amber-800");
    },
  );

  it("links only when an accessible scan and its status were supplied", () => {
    const props = {
      criticalRules: 0,
      highRules: 0,
      scanStatus: "COMPLETED" as const,
    };
    const href = "/@bob/fork/scans/123";
    expect(
      renderToStaticMarkup(<SecurityBadge {...props} scanHref={href} />),
    ).toContain(`href="${href}"`);
    expect(renderToStaticMarkup(<SecurityBadge {...props} />)).not.toContain(
      "href=",
    );
    expect(
      renderToStaticMarkup(
        <SecurityBadge {...props} scanStatus={null} scanHref={href} />,
      ),
    ).not.toContain("href=");
  });
});

const card: Card = {
  mirror: false,
  owner: "bob",
  name: "fork",
  owner_avatar_url: null,
  description: null,
  provider: null,
  visibility: "PUBLIC",
  origin_type: "FORK",
  forked_from: null,
  star_count: 0,
  fork_count: 0,
  updated_at: "2026-11-01T00:00:00Z",
  state: "PUBLISHED",
  failure: null,
  badge: {
    version: "v1",
    critical_rules: 1,
    high_rules: 0,
    unknown: 0,
    total: 1,
    scanned_at: null,
    scan_status: "COMPLETED",
    partial_result: true,
    scan_id: "123",
  },
};

describe("shared home, explore and profile card", () => {
  it("preserves the module target and adds a sibling scan link for a shared fork", () => {
    const html = renderToStaticMarkup(<RepositoryCard card={card} />);
    expect(html).toContain("위험 1종 · 부분 분석");
    expect(html).toContain('href="/@bob/fork"');
    expect(html).toContain('href="/@bob/fork/scans/123"');
    expect(html).not.toMatch(/<a\b[^>]*>(?:(?!<\/a>)[\s\S])*<a\b/);
  });

  it("does not fabricate a scan link or a complete result for legacy data", () => {
    const html = renderToStaticMarkup(
      <RepositoryCard
        card={{
          ...card,
          badge: {
            version: "v1",
            critical_rules: 0,
            high_rules: 0,
            unknown: 0,
            total: 0,
            scanned_at: null,
          } as Card["badge"],
        }}
      />,
    );
    expect(html).toContain("미검사");
    expect(html).not.toContain("주요 위험 없음");
    expect(html).not.toContain("/scans/");
  });

  it.each(["FAILED", "PENDING", "PUBLISHED"] as const)(
    "handles missing badge in %s",
    (state) => {
      const html = renderToStaticMarkup(
        <RepositoryCard card={{ ...card, state, badge: null }} />,
      );
      expect(html).toContain(
        state === "FAILED"
          ? "분석 실패"
          : state === "PENDING"
            ? "분석 중"
            : "미검사",
      );
      expect(html).not.toContain("/scans/");
    },
  );
});

it("keeps failure, known risk and the scan link on owner-only home/profile cards", () => {
  const html = renderToStaticMarkup(
    <FailedCard
      onChange={() => {}}
      card={{
        ...card,
        state: "FAILED",
        badge: { ...card.badge!, scan_status: "FAILED" },
      }}
    />,
  );
  expect(html).toContain("위험 1종 · 분석 실패 · 부분 분석");
  expect(html).toContain('href="/@bob/fork/scans/123"');
  expect(html).toContain("다시 시도");
});
