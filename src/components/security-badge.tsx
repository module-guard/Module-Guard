import { ShieldAlert, ShieldCheck } from "lucide-react";
import { Time } from "./live-shared";
import { Tooltip } from "./ui/tooltip";

/**
 * 일반 사용자에게 보여 주는 세 단계 배지. 숫자는 탐지 건수가 아니라 규칙 종류 수다.
 * "안전"이라고 단정하지 않고, 언제 어떤 버전을 검사했는지 항상 함께 보여 준다.
 */
export function SecurityBadge({
  criticalRules,
  highRules,
  unknown = 0,
  version,
  scannedAt,
}: {
  criticalRules: number;
  highRules: number;
  unknown?: number;
  version?: string | null;
  scannedAt?: string | null;
}) {
  const level = criticalRules ? "danger" : highRules ? "caution" : "none";
  const label =
    level === "danger"
      ? `위험 ${criticalRules}종`
      : level === "caution"
        ? `주의 ${highRules}종`
        : "주요 위험 없음";
  const style = {
    danger: "border-red-200 bg-red-50 text-red-700",
    caution: "border-amber-200 bg-amber-50 text-amber-800",
    none: "border-slate-200 bg-slate-100 text-slate-600",
  }[level];
  const Icon = level === "none" ? ShieldCheck : ShieldAlert;
  const detail = [
    level === "danger"
      ? `Critical 규칙 ${criticalRules}종이 탐지되었습니다.`
      : level === "caution"
        ? `High 규칙 ${highRules}종이 탐지되었습니다.`
        : "Critical·High 규칙이 탐지되지 않았습니다.",
    unknown > 0 && `심각도 미분류 ${unknown}건은 포함되지 않았습니다.`,
    "자세한 건수는 보안 탭에서 확인하세요.",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Tooltip content={detail}>
        <span
          tabIndex={0}
          className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-1 text-xs font-semibold ${style}`}
        >
          <Icon size={14} />
          {label}
        </span>
      </Tooltip>
      {scannedAt && (
        <span className="whitespace-nowrap text-xs text-slate-500">
          검사: {version} · <Time value={scannedAt} />
        </span>
      )}
    </span>
  );
}
