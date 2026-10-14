/** User-facing explanations; server details remain available in the scan response. */
export function explainScanError(message: string): string {
  if (/snapshot size limit/i.test(message))
    return "소스 크기가 보관 한도를 넘어 검사를 완료하지 못했습니다.";
  if (/snapshot could not be stored/i.test(message))
    return "소스를 보관하지 못해 검사를 완료하지 못했습니다. 잠시 뒤 다시 올려 주세요.";
  if (/prepared source/i.test(message))
    return "준비한 소스가 만료되었습니다. 다시 올려 주세요.";
  if (/no terraform/i.test(message))
    return "저장소에 Terraform 파일이 없습니다. 올바른 모듈 저장소인지 확인해 주세요.";
  if (/clone/i.test(message))
    return "Repository 수집에 실패했습니다. 공개 GitHub URL과 저장소 접근 가능 여부를 확인해 주세요.";
  if (/timeout|timed out/i.test(message))
    return "검사 제한 시간을 초과했습니다. 저장소 크기와 서버 상태를 확인해 주세요.";
  if (/checkov.*not.*install|checkov binary/i.test(message))
    return "Checkov가 설치되지 않았거나 실행 경로가 올바르지 않습니다. 시스템 상태를 확인해 주세요.";
  if (/checkov/i.test(message))
    return "Checkov 보안 검사에 실패했습니다. 완료된 정적 분석 결과는 아래에서 확인할 수 있습니다.";
  return message;
}
