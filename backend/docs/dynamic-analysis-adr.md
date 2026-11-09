# ADR: 격리 동적 분석은 별도 worker로 구현한다 (#36)

상태: 설계·비실행 계약. 실제 worker, Terraform 명령, 클라우드 생성, 자격증명 사용은
이 변경에 포함하지 않는다. 현 저장소의 init/plan/apply/test 금지는 유지된다.
`terraform plan`도 provider 바이너리를 실행할 수 있다. mock은 격리 수단이 아니다.

## 경계와 위협 모델

API 서버는 서명된 입력 digest와 승인된 실행 정책만 큐에 전달한다. 비신뢰 모듈은
API 프로세스에서 실행하지 않는다. 향후 worker는 작업마다 폐기하는 별도 격리
환경(운영 승인된 microVM 또는 강화된 sandbox)으로 한다. Docker 컨테이너만으로
격리를 충족한다고 주장하지 않는다. 공격자는 provider/test 코드, 외부 다운로드,
무한 루프, 대용량 로그, 경로 탈출, DNS·metadata endpoint, 비밀값 유출을 제어할 수 있다.

- 기본 자격증명 없음. 호스트 환경변수 상속 금지, AWS/클라우드 metadata 차단.
- 호스트 파일시스템·Docker socket 마운트 금지. 읽기 전용 digest 고정 입력,
  작업별 임시 디스크, rootless UID, 권한 상승·장치 접근 금지.
- 네트워크 egress 기본 거부(DNS 포함). 승인된 미러만 별도 수집 단계에서 사용하며
  실행 중 provider 다운로드 금지. provider/이미지/lockfile digest 고정 및 검증.
- 기본 상한 제안: 1 vCPU, 512MiB RAM, 1GiB 임시 디스크, 프로세스 64개,
  60초 벽시계, 로그 1MiB. 실제 적용 여부는 worker 승인 시험에서 증명해야 한다.
- timeout/cancel/worker crash는 외부 감독자가 VM과 자식 프로세스를 강제 종료한다.
  정리 완료를 확인하기 전 리소스 슬롯을 재사용하지 않는다.
- 원문 입력·환경변수·provider stdout/stderr는 API/로그/브라우저로 전달하지 않는다.
  현재 계약은 코드 allowlist와 opaque 근거 ID만 허용한다. 임의 로그 필드는 금지한다.
- 실제 클라우드 실행은 별도 비용 상한, 작업별 단기 최소권한, 계정 격리,
  명시적 실행 정책·승인·청소 검증을 요구한다. 기본 정책에서 금지한다.

## 이벤트·재사용 계약

`dynamic-event.schema.json`은 `app.schemas.dynamic.DynamicRun`에서 생성한다.
`python -m scripts.export_dynamic_contract`는 JSON Schema와 6개 synthetic fixture만
생성한다. Terraform이나 worker를 호출하지 않는다.

run_id, 입력/manifest digest, 이미지 digest, Terraform/provider/lockfile/test suite/mock
버전, 정책, 단계별 시작·종료, 결과, 관측 노드·연결, 정리 단계, 비밀 제거 정책을
포함한다. 상태: success/failed/aborted/timeout/not_run/unobservable.
fixture는 execution_occurred=false이며 가상 시간은 실제 이벤트가 아니다.
mock은 runtime 관측으로 바꿀 수 없다. 실행하지 않은 상태에는 시각·관측을 넣을 수 없다.
시각 역전, 중복 ID, 근거 누락, 없는 노드로 연결, 부분 단계를 성공으로 승격하는 입력을
거부한다. 원문 로그 필드를 추가하면 extra=forbid 검증이 실패한다.

정적 검사 재사용 키와 동적 키는 공유하지 않는다. 동적 재사용은 아직 허용하지 않는다.
향후 입력·이미지·도구·provider/lockfile·test suite/mock·정책·환경·자격증명 범위 중
하나라도 다르거나 확인 불가하면 재사용을 거부한다.

## 승인·종료 조건과 테스트 전략

후속 worker 이슈를 별도로 만들고 운영/보안 담당의 격리 설계 검토와 실행 금지 지침의
명시적 예외 승인을 받아야 한다. 이 ADR은 실행 승인이 아니다.
호스트/metadata/socket/egress 접근 시도, fork bomb, 디스크·메모리·로그 폭주,
timeout, cancel, 감독자/worker 비정상 종료, 비밀값 삽입, digest 변조를 시험한다.
탈출·비밀 유출·정리 실패·비용 상한 초과 중 하나라도 있으면 실험을 중단하고
자격증명을 폐기하고 환경을 격리한다. 모든 자식 프로세스/임시 디스크/네트워크 세션과
클라우드 자원이 없어졌다는 검증까지 작업을 종료하지 않는다.

현재 자동 검증은 비실행 fixture 계약과 API, UI 범례·상태·근거 탐색에 한정한다.
실제 실행 연동과 운영 활성화는 후속 작업이다.
