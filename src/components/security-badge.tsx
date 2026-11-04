import Link from "next/link";
import type { components } from "@/api/generated/schema";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { Time } from "./live-shared";
import { Tooltip } from "./ui/tooltip";

/**
 * 위험 수준과 분석 완전성을 함께 표시하는 배지. 숫자는 탐지 건수가 아니라 규칙 종류 수다.
 * "안전"이라고 단정하지 않고, 언제 어떤 버전을 검사했는지 항상 함께 보여 준다.
 */
export function SecurityBadge({
  criticalRules,
  highRules,
  unknown = 0,
  version,
  scannedAt,
  scanStatus,
  partialResult = false,
  scanHref,
}: {
  criticalRules: number;
  highRules: number;
  unknown?: number;
  version?: string | null;
  scannedAt?: string | null;
  scanStatus?: components["schemas"]["ScanStatus"] | null;
  partialResult?: boolean;
  /** Only supplied for a scan returned by the access-controlled API. */
  scanHref?: string | null;
}) {
  const level = criticalRules ? "danger" : highRules ? "caution" : "none";
  const completeness =
    scanStatus === "FAILED"
      ? "분석 실패"
      : !scanStatus
        ? "미검사"
        : scanStatus !== "COMPLETED"
          ? "분석 중"
          : partialResult
            ? "부분 분석"
            : null;
  const risk = criticalRules
    ? `위험 ${criticalRules}종`
    : highRules
      ? `주의 ${highRules}종`
      : null;
  const label =
    [
      risk,
      completeness,
      partialResult && completeness !== "부분 분석" ? "부분 분석" : null,
    ]
      .filter(Boolean)
      .join(" · ") || "주요 위험 없음";
  const style = {
    danger: "border-red-200 bg-red-50 text-red-700",
    caution: "border-amber-200 bg-amber-50 text-amber-800",
    none: "border-slate-200 bg-slate-100 text-slate-600",
  }[level];
  const Icon = level === "none" && !completeness ? ShieldCheck : ShieldAlert;
  const detail = [
    level === "danger"
      ? `Critical 규칙 ${criticalRules}종이 탐지되었습니다.`
      : level === "caution"
        ? `High 규칙 ${highRules}종이 탐지되었습니다.`
        : completeness
          ? `${completeness}: 전체 검사 완료를 확인할 수 없습니다.`
          : "Critical·High 규칙이 탐지되지 않았습니다.",
    partialResult && "일부 파일 또는 도구의 분석 결과가 누락되었습니다.",
    unknown > 0 && `심각도 미분류 ${unknown}건은 포함되지 않았습니다.`,
    "자세한 건수는 보안 탭에서 확인하세요.",
  ]
    .filter(Boolean)
    .join(" ");
  const badgeClass = `inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-semibold ${style}`;
  const content = (
    <>
      <Icon size={14} />
      {label}
    </>
  );
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Tooltip content={detail}>
        {scanHref && scanStatus ? (
          <Link
            href={scanHref}
            className={`${badgeClass} relative z-10`}
            aria-label={`${label} · 검사 상세 보기`}
          >
            {content}
          </Link>
        ) : (
          <span tabIndex={0} className={badgeClass}>
            {content}
          </span>
        )}
      </Tooltip>
      {scannedAt && (
        <span className="whitespace-nowrap text-xs text-slate-500">
          검사: {version} · <Time value={scannedAt} />
        </span>
      )}
    </span>
  );
}
