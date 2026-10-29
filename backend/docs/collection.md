# 소스 수집 (Collection)

수집기(`app/collectors/`)는 사용자가 제출한 소스를 작업 공간으로 가져오는 계층입니다. 각 수집기는
`SourceCollector` 인터페이스(`supports`, `collect`)를 구현하며, `SourceRouter`가 입력 형식을 보고
알맞은 수집기를 고릅니다. 이후 단계(Checkov, 파서, 그래프, 분석)는 수집 방식과 무관하게 동일합니다.

## 지원 입력 형식

| 형식 | 예시 | 수집기 |
|---|---|---|
| 공개 GitHub 저장소 URL | `https://github.com/owner/repo` | `GitHubCollector` |
| Terraform Registry 주소 | `namespace/name/provider` (예: `terraform-aws-modules/vpc/aws`) | `TerraformRegistryCollector` |
| zip 업로드 | 브라우저 업로드 | `PublishService.prepare_zip` |

- GitHub URL은 정확히 `https://github.com/owner/repo` 형식만 허용합니다(`normalize_github_url`).
  `http`, SSH, 쿼리/프래그먼트, 경로 세그먼트 추가는 거부합니다.
- Registry 주소는 `namespace/name/provider` 세 조각만 받습니다. 모듈의 실제 코드는 Registry가 아니라
  해당 모듈이 가리키는 **GitHub 저장소에서 clone**합니다. Registry에는 작은 메타데이터 JSON만 조회합니다.
- 비공개 저장소, 인증 토큰, 외부 module 재귀 다운로드는 지원하지 않습니다.

## Terraform Registry 수집 흐름

1. 주소를 `namespace`/`name`/`provider`로 분리합니다.
2. `GET {REGISTRY_BASE_URL}/v1/modules/{namespace}/{name}/{provider}` (버전 지정 시 `.../{version}`)로
   메타데이터 JSON을 조회해 원본 저장소(`source`)와 버전(`version`)을 얻습니다.
3. 원본이 GitHub가 아니면 `UnsupportedSourceError`(422)입니다. 원본 URL은 `normalize_github_url`로 검증합니다.
4. `GitHubCollector.collect(url, destination, ref=...)`로 clone합니다. 태그는 먼저 `v{version}`,
   실패하면 `{version}`을 시도하며, 둘 다 실패하면 `RepositoryCloneError`입니다.
5. 반환하는 `CollectedModule`의 `source_type`은 `TERRAFORM_REGISTRY`, `source_url`은 Registry 주소,
   `commit`은 clone한 실제 커밋 해시입니다.

### 네트워크 안전(SSRF 방지)

- 요청 호스트는 설정값 `REGISTRY_BASE_URL`로 고정되며 사용자 입력으로 바뀌지 않습니다. 경로는 정규식으로
  제한된 주소 조각과 `x.y.z` 형식 버전만으로 구성합니다.
- `follow_redirects=False`: 리다이렉트(3xx)는 따라가지 않고 실패로 처리합니다.
- 응답 본문은 **1MB**까지만 읽습니다(초과 시 거부).
- 버전 문자열은 `x.y.z`(각 1~5자리 숫자) 형식만 허용합니다. 그 외(`5`, `5.8`, `v5.8.1`, `latest` 등)는
  네트워크 요청 전에 `UnsupportedSourceError`(422)로 거부합니다.
- 전송 계층 오류 메시지는 응답·로그에 노출하지 않습니다.

## API

| 메서드 | 경로 | 본문 / 비고 |
|---|---|---|
| POST | `/api/uploads/registry` | `RegistryImportRequest`: `{"address": "namespace/name/provider", "version": "5.8.1" \| null}`. 응답은 `/api/uploads/github`와 같은 `UploadReport`. |
| POST | `/api/uploads/github` | `{"url": "https://github.com/owner/repo"}` |
| POST | `/api/uploads/zip` | 본문이 zip 자체 |
| POST | `/api/scans` | 개발 모드 전용. `{"source": "..."}`에 GitHub URL 또는 Registry 주소를 받습니다. |
| GET | `/api/scans/{scan_id}` | `ScanResponse`. 아래 `failed_stage`/`error_code` 참고. |

`/api/uploads/registry`는 `/github`와 동일하게 로그인 확인, `Origin` 검사, 사용자당 동시 실행 제한
(`service.limiter.slot`)을 적용합니다.

### 오류 형식

도메인 오류는 `{"error": {"code": "...", "message": "..."}}` 형식입니다. 요청 스키마 검증 오류는 FastAPI 기본
`422 detail` 형식입니다.

| 상황 | 상태 | code |
|---|---|---|
| 주소·버전 형식 오류 | 422 | `UnsupportedSourceError` / 스키마 `detail` |
| Registry에 해당 모듈 없음 | 404 | `RegistryModuleNotFoundError` |
| 원본이 GitHub가 아님 | 422 | `UnsupportedSourceError` |
| 원본 저장소 clone 실패 / Registry 응답 이상(리다이렉트·과대·비 JSON) | 502 | `RepositoryCloneError` |

## 검사 실패 단계 기록

검사가 `FAILED`로 끝나면 `GET /api/scans/{scan_id}` 응답에 다음이 채워집니다. 새 DB 컬럼은 만들지 않고
기존 `scans.analysis` JSONB에 `"failure": {"stage", "code"}`로 저장한 뒤 응답에서 꺼냅니다.

- `failed_stage`: 실패한 파이프라인 단계.
  `"collect" | "checkov" | "parse" | "graph" | "source_trust" | "data_flow" | "correlation" | "persistence"`
  중 파이프라인이 도달한 가장 마지막 단계. 어떤 단계도 시작하기 전에 실패하면 `"collect"`.
- `error_code`: 예외 클래스 이름(예: `"RepositoryCloneError"`, `"ScanTimeoutError"`).

`error_message`에는 외부 도구의 stderr 등 신뢰할 수 없는 텍스트를 노출하지 않는 기존 정책을 유지합니다.
검사가 `COMPLETED`이면 두 값 모두 `null`입니다.

## 중단된 검사 복구 (Recovery)

API/worker 프로세스가 강제 종료되면 DB에 `QUEUED`·`COLLECTING`·`SCANNING`·`NORMALIZING` 기록이
남습니다. 배포는 단일 uvicorn 프로세스(`--workers 1`) + 스레드 풀이므로, 재시작 시점에는 정상 실행
중인 검사가 있을 수 없습니다. 따라서 production에서 dispatcher가 만들어질 때(`InProcessScanDispatcher`
생성자) `ScanService.recover_interrupted`가 1회 실행됩니다. main.py는 수정하지 않습니다. 개발·테스트
환경(`app_environment != "production"`)에서는 자동 실행하지 않고, 필요 시 직접 호출합니다.

- `QUEUED`: 준비 소스(`claimed/<scan_id>`)가 그대로 있으므로 dispatcher에 **다시 등록**합니다. 실제
  실행은 worker의 `claim()`(QUEUED→COLLECTING)이 한 번만 성공시키므로, 동시 복구·중복 제출에도
  한 번만 실행됩니다. 큐가 가득 차면 `FAILED`(`code=QueueUnavailable`, `stage=collect`)로 종결합니다.
- `COLLECTING`/`SCANNING`/`NORMALIZING`: 준비 소스는 이미 수집되어 사라졌을 수 있으므로 **재실행하지
  않고** `FAILED`로 종결합니다. 상태 전환은 조건부 UPDATE(`status IN (…)`)로 원자적이라 한 번만
  성공합니다. `failure.stage`는 도달한 마지막 단계(`analysis.stages` 기준, 없으면 `collect`),
  `failure.code`는 `Interrupted`입니다. 기존 `analysis.stages`·`partial_result`·findings는 보존합니다.

여러 프로세스로 배포할 경우(`scan_recovery_assume_single_worker=false`)에는 `analysis.runtime.heartbeat`가
`scan_recovery_stale_seconds`보다 오래된 검사만 복구하고, heartbeat가 아직 신선한 검사는 그 검사를 돌리는
다른 worker의 것으로 보고 건드리지 않습니다. run()은 claim 시점과 각 단계 커밋에서 `analysis.runtime`
(`owner`/`attempt`/`heartbeat`)을 갱신합니다. 이 값들은 새 컬럼 없이 `analysis` JSONB에만 저장되며
`AnalysisSummary` 스키마에 없으므로 응답에는 노출되지 않습니다.

## 검사 재시도 (Retry)

| 메서드 | 경로 | 본문 / 비고 |
|---|---|---|
| POST | `/api/scans/{scan_id}/retry` | `{"upload_id": "<새로 준비한 소스>", "acknowledge_warnings": false}`. 로그인·`Origin` 검사·소유자 확인 적용. |

실패한 검사를 **새로 준비한 소스**로 다시 시도합니다(원래 준비 소스는 이미 소비되었으므로 재업로드가
필요합니다). 기존 `module_version`을 **재사용**하므로 저장소·버전·findings가 중복 생성되지 않고, 이전 실패
검사는 그대로 보존한 채 새 시도와 연결됩니다(`old.analysis.retried_by`, `new.analysis.retry_of`; 응답의
`retried_by`/`retry_of`로도 노출). 멱등성은 조건부 UPDATE
(`module_versions.scan_id`를 `NULL` 또는 이전 scan일 때만 새 scan으로 전환)로 보장하여, 같은 실패 검사에
대한 반복·동시 재시도가 새 시도를 하나만 만듭니다.

적용 순서와 오류:

| 상황 | 상태 | code |
|---|---|---|
| 성공(수락) | 202 | 응답 `{"scan_id","status":"QUEUED"}`, `Location` 헤더 |
| 다른 사용자/소유자 아님 | 404 | `NotFoundError` (비공개 검사는 가시성 단계에서 숨김) |
| `Origin` 불일치 | 403 | `ForbiddenOriginError` |
| 실패 상태가 아님 | 409 | `ConflictError` |
| 이미 재시도됨(반복·동시) | 409 | `ConflictError` |
| 준비 소스 만료/소실 | 404 | `StagedUploadNotFoundError` ("다시 올려 주세요") |
| 동시 실행 제한 초과 | 429 | `RateLimitedError` |
| 큐 포화 | 503 | `ScanQueueFullError` (새 scan을 만들기 전에 거부하므로 상태 오염 없음) |

고착된 검사가 복구로 `FAILED`가 되면 더 이상 진행 중으로 집계되지 않으므로, 소유자의 동시 실행 제한
(`max_running_publishes`)에 걸려 막혀 있던 새 업로드·재시도가 다시 허용됩니다.

## 설정값

| 변수 | 기본값 | 용도 |
|---|---|---|
| `REGISTRY_BASE_URL` | `https://registry.terraform.io` | Registry 메타데이터 조회 호스트. `https://`만 허용. |
| `REGISTRY_TIMEOUT_SECONDS` | `10` | Registry 요청 타임아웃(초). |
| `GIT_TIMEOUT_SECONDS` | `120` | 각 Git 명령 타임아웃. clone 단계에 적용. |
| `MAX_REPOSITORY_SIZE_MB` | `200` | clone 저장소 최대 크기(`.git` 포함). |
| `MAX_UPLOAD_UNCOMPRESSED_MB`, `MAX_UPLOAD_FILES` | `50`, `1000` | 준비된 소스 트리의 크기·파일 수 상한. Registry 가져오기에도 동일 적용. |
| `SCAN_RECOVERY_ASSUME_SINGLE_WORKER` | `true` | 단일 프로세스 배포 가정. `true`면 재시작 시 비종료 검사를 모두 복구. 여러 프로세스면 `false`로 두어 heartbeat 기준으로만 복구. |
| `SCAN_RECOVERY_STALE_SECONDS` | `900` | 멀티 프로세스에서 heartbeat가 이 시간보다 오래되면 중단으로 간주. 최장 단계(checkov+git)보다 크게 둠. |

응답 본문 1MB 상한은 `app/collectors/terraform_registry.py`의 `MAX_RESPONSE_BYTES` 상수입니다.
