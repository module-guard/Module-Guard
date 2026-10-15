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

## 설정값

| 변수 | 기본값 | 용도 |
|---|---|---|
| `REGISTRY_BASE_URL` | `https://registry.terraform.io` | Registry 메타데이터 조회 호스트. `https://`만 허용. |
| `REGISTRY_TIMEOUT_SECONDS` | `10` | Registry 요청 타임아웃(초). |
| `GIT_TIMEOUT_SECONDS` | `120` | 각 Git 명령 타임아웃. clone 단계에 적용. |
| `MAX_REPOSITORY_SIZE_MB` | `200` | clone 저장소 최대 크기(`.git` 포함). |
| `MAX_UPLOAD_UNCOMPRESSED_MB`, `MAX_UPLOAD_FILES` | `50`, `1000` | 준비된 소스 트리의 크기·파일 수 상한. Registry 가져오기에도 동일 적용. |

응답 본문 1MB 상한은 `app/collectors/terraform_registry.py`의 `MAX_RESPONSE_BYTES` 상수입니다.
