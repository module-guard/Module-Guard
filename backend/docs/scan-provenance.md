# 검사별 분석 버전 메타데이터 (#13)

`GET /api/scans/{scan_id}`의 선택 필드 `provenance`와 `postprocessing`은 검사 시점과 후처리 시점의 정보를 분리한다. 기존 레코드에서 없는 값은 `null`로 유지하며 현재 버전으로 소급 채우지 않는다. 기존 `scans.analysis` JSON 컬럼을 사용하므로 migration은 없다. `analysis` 응답의 기존 필드/스테이지 구조는 변경하지 않는다.

- `provenance.moduleguard_version`: 설치된 백엔드 패키지 배포 버전. 배포 메타데이터를 읽을 수 없으면 `null`.
- `provenance.build_commit`: 배포에서 `MODULEGUARD_BUILD_SHA`를 40/64자리 hex로 명시 주입했을 때만 기록하며 없으면 `null`. 소스 저장소의 `Scan.commit_hash`(검사 대상 Git commit)와는 다르다.
- `provenance.checkov_version`: 실행 대상 Checkov 바이너리를 `--version`으로 조회한 실제 버전. Fixture scanner 또는 버전 조회 실패·비정상 출력이면 `null`. 조회 실패가 검사 자체를 실패시키지 않는다. `ProcessRunner`로 10초/4KiB 상한과 격리된 환경에서 실행하며 오류 원문을 기록하지 않는다.
- `provenance.severity_rules_version`, `remediation_rules_version`: 배포된 각 규칙 파일 바이트의 SHA-256. `rules_version`: 배포된 Checkov 실행 설정/normalizer/severity/remediation/scope/source-trust/correlation 관련 지정 파일들의 경로와 바이트를 순서대로 해시한 값. 파일이 없으면 `null`. **규칙 코드의 변경 표식**이지 보안 증명이나 전체 빌드의 재현 가능한 해시가 아니다. analyzer/parser 또는 외부 도구의 모든 변경까지 포괄하지 않는다.
- `provenance.analysis_options`: 검사 결과에 영향을 줄 수 있는 공개 정수 상한/시간 제한만 allowlist로 기록한다. 환경변수, 경로, 인증정보는 제외한다. `checkov_options`는 실행할 때 강제되는 공개된 프레임워크 및 옵션을 기록한다.
- `postprocessing`: reapply가 실제 finding 또는 검사 카운터/범위 요약을 수정한 때의 UTC 시각과 당시 규칙 버전. `history`는 이전 후처리 기록이며 반복 실행에서 아무 값도 바뀌지 않으면 갱신하지 않는다. 최초 검사 때의 `provenance`는 덮어쓰지 않는다. correlation은 reapply하지 않으므로 과거 correlation 결과가 새 규칙으로 재평가됐다는 뜻은 아니다.

예시 (일부 값 생략):

```json
{
  "provenance": {
    "moduleguard_version": "0.1.0",
    "build_commit": null,
    "scanner_name": "checkov",
    "checkov_version": "3.3.26",
    "severity_rules_version": "<SHA-256>",
    "remediation_rules_version": "<SHA-256>",
    "rules_version": "<SHA-256>",
    "analysis_options": {"max_analysis_entities": 20000},
    "checkov_options": {"framework": "terraform", "skip_download": true}
  },
  "postprocessing": null
}
```

같은 source commit이라도 도구나 규칙 해시가 다르면 검사 당시 조건의 차이를 구분할 수 있다. 단, 외부 환경·동시성·배포의 비기록 요소에 따라 결과는 달라질 수 있으므로 **비트 단위 결과 재현을 보장하지 않는다.** 이 메타데이터는 #9의 소스/ZIP 무결성 검증, 전자서명 또는 머클 로그가 아니다. 프론트엔드가 해당 필드를 표시하거나 생성된 API 타입을 갱신하려면 담당자와 계약을 조율해야 한다.
