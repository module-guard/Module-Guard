# 공개 근거 정책 evidence-v1 (#33/#34)

총점은 제공하지 않는다. 독립 평가 상태는 `not_independently_validated`이다.
GitHub: https://docs.github.com/en/rest/repos/repos#get-a-repository
Registry: https://developer.hashicorp.com/terraform/registry/api-docs

공식 공개 REST API만 사용한다. GitHub 인증 자격증명을 보내지 않고 public 응답만
허용한다. 호스트·경로를 고정하고 리디렉션을 따라가지 않는다. 응답 512KiB,
요청당 8초 제한, 서버 오류·일시 통신 오류 1회 재시도, 403/429 재시도 없음.
버전/시간 버킷(1시간)별 결과와 실패를 저장한다. 동시 저장은 unique constraint로
단일 스냅샷을 유지한다. 소유자만 Origin 검증과 rate limit을 거쳐 수집할 수 있다.
읽기와 이력은 기존 repository visibility를 적용한다. 원자료는 지정 필드만 보존한다.

manifest에서 SECURITY.md, README, test 파일 존재를 측정한다. 이것은 운영·문서 품질,
테스트 통과를 증명하지 않는다. GitHub archived는 명시적 보관 상태이며 pushed_at이
오래되었다는 이유로 관리 중단이라고 판단하지 않는다. 신규·작은 프로젝트에 활동량
점수를 부과하지 않는다. 별·다운로드는 인기도 영역이며 계산에 포함하지 않는다.

근거 충족률 = confirmed 수 / not_applicable 제외 항목 수 × 100. 이 값은 긍정적
근거 비율이 아니라 관측 가능성이다. false/0, 정보 부족, 실패, 미확인은 구분한다.
7일 경과 자료는 stale로 표시한다. 과거 기록의 충족률은 수집 당시 수치이다.
서명 실패·위험 진단은 평판으로 상쇄하지 않는다.

## 독립 평가 절차와 현황

`reputation-cohort.json`은 실제 평가 결과가 아닌 30개 표본의 모집 슬롯이다.
공급자 5개, 프로젝트 규모 2개, 신규/활성/보관 3개 층으로 나눈다. 보정/평가를
분리하고 동일 프로젝트가 두 집합에 들어가지 않게 한다. 슬롯의 module_url,
evidence_digest, reviewer_a, reviewer_b는 아직 null이다. 두 평가자는 서로의
결과를 보기 전에 근거별 판단과 누락 이유를 기입한다. 불일치는 원자료와 정책을
대조하여 기록하고 평가 집합으로 정책을 보정하지 않는다.

현재 확인: 동일 입력 결정성, 인기도 변경이 coverage에 영향 없음, 누락과 0 구분의
자동 회귀 테스트. 아직 미실시: 실제 30개 표본 수집, 두 평가자 판단, 집단별 편향과
일치도 수치 분석. 이를 완료로 주장하지 않는다. 실제 자료가 모이면 축별 불일치율,
Cohen's kappa(적용 가능한 범주형 항목), 신규·소규모 결측률을 보고하고 총점 공개를
별도 검토한다. 악의적 파일 생성으로 존재 지표를 조작할 수 있어 보안 등급으로
사용하지 않는다. 테스트 결과나 운영 수준을 이 지표에서 추론하지 않는다.
