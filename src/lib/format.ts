const units: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 31536000],
  ["month", 2592000],
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
];
const rtf = new Intl.RelativeTimeFormat("ko", { numeric: "always" });
/** `3분 전`처럼 상대 시간으로 바꾼다. 1분 이내는 `방금 전`. */
export function relativeTime(value: string | Date, now = Date.now()) {
  const seconds = Math.round((new Date(value).getTime() - now) / 1000);
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size)
      return rtf.format(Math.trunc(seconds / size), unit);
  }
  return "방금 전";
}
export function exactTime(value: string | Date) {
  return new Date(value).toLocaleString("ko-KR");
}
