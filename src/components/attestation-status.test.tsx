import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

const state = vi.hoisted(() => ({
  timeline: {
    data: {
      scan_id: "scan",
      version_id: "version",
      signature_status: "unsigned",
      events: [],
      unrecorded: [],
      observed_at: "now",
    },
    error: "",
    retry: () => {},
  },
  proof: {
    data: {
      policy_enabled: false,
      scan_id: "scan",
      version_id: "version",
      status: "unsigned",
      reasons: ["NO_SIGNATURE"],
    },
    observedAt: "now",
    error: "",
    retry: () => {},
  },
  policyKeys: [] as string[],
}));
vi.mock("@/api/hooks", () => ({
  useApi: (_load: unknown, key: string) => {
    if (key.endsWith("/attestation-policy")) {
      state.policyKeys.push(key);
      return {
        data: {
          observedAt: state.proof.observedAt,
          response: state.proof.data,
        },
        error: state.proof.error,
        retry: state.proof.retry,
      };
    }
    return state.timeline;
  },
}));
vi.mock("./graph/graph-canvas", () => ({ GraphCanvas: () => null }));
import { VerificationTimeline } from "./trust-explorer";

const props = { owner: "alice", name: "demo", version: "v1" };
describe("attestation policy visibility", () => {
  it("labels an unconfigured signer as disabled, not merely unsigned", () => {
    state.proof.data = {
      policy_enabled: false,
      scan_id: "scan",
      version_id: "version",
      status: "unsigned",
      reasons: ["NO_SIGNATURE"],
    };
    const html = renderToStaticMarkup(<VerificationTimeline {...props} />);
    expect(html).toContain("서명 비활성");
    expect(html).not.toContain("서명 없음");
  });

  it("labels no proof under a configured policy as unsigned", () => {
    state.proof.data = {
      policy_enabled: true,
      scan_id: "scan",
      version_id: "version",
      status: "unsigned",
      reasons: ["NO_SIGNATURE"],
    };
    const html = renderToStaticMarkup(<VerificationTimeline {...props} />);
    expect(html).toContain("서명 없음");
    expect(html).not.toContain("서명 비활성");
  });

  it("never shows a cached policy for a different scan as active", () => {
    state.proof.data = {
      policy_enabled: true,
      scan_id: "old-scan",
      version_id: "version",
      status: "valid",
      reasons: [],
    };
    const html = renderToStaticMarkup(<VerificationTimeline {...props} />);
    expect(html).toContain("서명 정책 확인 불가");
    expect(html).not.toContain("서명 없음");
  });

  it("does not show valid while the policy lookup is stale", () => {
    state.timeline.data.signature_status = "valid";
    state.proof.data = {
      policy_enabled: true,
      scan_id: "old-scan",
      version_id: "version",
      status: "valid",
      reasons: [],
    };
    const html = renderToStaticMarkup(<VerificationTimeline {...props} />);
    expect(html).toContain("검증 상태 확인 불가");
    expect(html).not.toContain("현재 서명 상태: valid");
    state.timeline.data.signature_status = "unsigned";
  });

  it.each(["invalid", "revoked", "unknown"])(
    "preserves a %s result even when the policy is unavailable",
    (status) => {
      state.proof.data = {
        policy_enabled: false,
        scan_id: "scan",
        version_id: "version",
        status,
        reasons: ["TARGET_MISMATCH"],
      };
      const html = renderToStaticMarkup(<VerificationTimeline {...props} />);
      expect(html).toContain(`현재 서명 상태: ${status}`);
      expect(html).toContain("서명 비활성");
      expect(html).toContain("TARGET_MISMATCH");
    },
  );

  it("uses the proof response status instead of a mismatched timeline snapshot", () => {
    state.proof.data = {
      policy_enabled: true,
      scan_id: "scan",
      version_id: "version",
      status: "invalid",
      reasons: ["TARGET_MISMATCH"],
    };
    const html = renderToStaticMarkup(<VerificationTimeline {...props} />);
    expect(html).toContain("현재 서명 상태: invalid");
    expect(html).not.toContain("서명 없음");
  });

  it("refreshes proof when the timeline observation changes", () => {
    state.policyKeys.length = 0;
    state.timeline.data.observed_at = "first";
    renderToStaticMarkup(<VerificationTimeline {...props} />);
    state.timeline.data.observed_at = "second";
    renderToStaticMarkup(<VerificationTimeline {...props} />);
    expect(state.policyKeys[0]).not.toBe(state.policyKeys[1]);
  });
});
