// 화면에 보이는 상태·심각도 표기와 색 규칙. 보라(브랜드색)는 진행 중에만 쓴다.
export type Tone =
  | "red"
  | "orange"
  | "yellow"
  | "blue"
  | "gray"
  | "faint"
  | "brand"
  | "green";
export const toneClass: Record<Tone, string> = {
  red: "border-red-200 bg-red-50 text-red-700",
  orange: "border-orange-200 bg-orange-50 text-orange-700",
  yellow: "border-amber-200 bg-amber-50 text-amber-800",
  blue: "border-blue-200 bg-blue-50 text-blue-700",
  gray: "border-slate-200 bg-slate-100 text-slate-600",
  faint: "border-slate-200 bg-white text-slate-400",
  brand: "border-violet-200 bg-violet-50 text-brand",
  green: "border-emerald-200 bg-emerald-50 text-emerald-700",
};
const known: Record<string, { label: string; tone: Tone; hint?: string }> = {
  // 심각도
  CRITICAL: { label: "Critical", tone: "red" },
  HIGH: { label: "High", tone: "orange" },
  MEDIUM: { label: "Medium", tone: "yellow" },
  LOW: { label: "Low", tone: "blue" },
  INFO: { label: "Info", tone: "gray" },
  UNKNOWN: {
    label: "미분류",
    tone: "faint",
    hint: "검사 도구가 심각도를 제공하지 않은 탐지입니다.",
  },
  // 검사·단계 상태
  COMPLETED: { label: "완료", tone: "gray" },
  FAILED: { label: "실패", tone: "red" },
  PARTIAL: {
    label: "부분 분석",
    tone: "yellow",
    hint: "일부 동적 Terraform 표현식은 정적 분석에서 완전히 해석되지 않았습니다.",
  },
  QUEUED: { label: "대기", tone: "brand" },
  PENDING: { label: "대기", tone: "faint" },
  COLLECTING: { label: "수집 중", tone: "brand" },
  SCANNING: { label: "검사 중", tone: "brand" },
  NORMALIZING: { label: "정리 중", tone: "brand" },
  RUNNING: { label: "진행 중", tone: "brand" },
  SKIPPED: { label: "건너뜀", tone: "faint" },
  // 시스템 상태
  Connected: { label: "연결됨", tone: "green" },
  ok: { label: "정상", tone: "green" },
  available: { label: "사용 가능", tone: "green" },
  unavailable: { label: "사용 불가", tone: "red" },
  degraded: { label: "일부 장애", tone: "red" },
};
export function describe(value: string) {
  return known[value] ?? { label: value, tone: "gray" as Tone };
}
