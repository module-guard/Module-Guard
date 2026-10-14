# Terraform Module Intelligence Backend

현재 범위는 **Checkov configuration scanning + Terraform entity parsing + Dependency Graph + Source Trust analysis + static Data Flow + Risk Correlation**입니다. 공개 GitHub 소스를 수집하고 Checkov로 Terraform 보안 설정을 검사해 표준화된 결과를 PostgreSQL에 저장합니다. Terraform 실행, 외부 module 재귀 다운로드, taint analysis, 실행 시 공격 판정과 trust scoring은 구현하지 않습니다.

2차 기능의 상세 설계·DB·규칙·API·예제·제한은 [Static analysis 문서](docs/static-analysis.md)를 참고하세요.

## 아키텍처

```text
Frontend / API client
  → FastAPI routes → ScanService.create → PostgreSQL (QUEUED)
  → ScanDispatcher → ScanService.run
  → SourceCollector → UUID workspace → Terraform 파일/경로 검증
  → SecurityScanner (Checkov CLI) → FindingNormalizer
  → TerraformParser → Entity / Reference → DependencyGraphBuilder
  → SourceTrustAnalyzer + DataFlowAnalyzer / DataFlowGraphBuilder
  → Repository → PostgreSQL → REST 조회
```

- `api/routes/`: 입력·필터·응답 계약. POST는 async, DB 작업은 thread pool로 넘깁니다. 동기 GET도 FastAPI의 thread pool에서 실행합니다.
- `services/`: ScanService가 실행 상태·트랜잭션·작업 공간 수명을 관리합니다. 요청과 별도 세션을 사용합니다.
- `services/dispatcher.py`: 동시 실행 수와 대기열 크기를 제한하는 in-process 실행기. Celery/RQ 실행기로 교체할 수 있는 인터페이스입니다.
- `collectors/`: `SourceCollector`, GitHub 구현, Registry 인터페이스, URL 정규화.
- `scanners/`: `SecurityScanner`, Checkov CLI, `FindingNormalizer`, severity mapping.
- `repositories/`, `models/`, `schemas/`: SQLAlchemy 저장 계층, DB 모델, Pydantic 응답.
- `workspace/`, `utils/`: UUID 작업 공간·subprocess·파일 경계 검사.
- `analyzers/base.py`: 공통 `ModuleAnalyzer` 계약. `SourceTrustAnalyzer`가 TFSC 규칙을 실행합니다.

프론트엔드는 실제 API와 연결되어 있습니다. FastAPI OpenAPI에서 TypeScript 계약을 생성하며 API 변경 후 반드시 root에서 `npm run api:sync`를 실행합니다. `npm run api:check`와 CI가 갱신 누락을 검출합니다. 전체 실행과 UI 시연 절차는 [root README](../README.md)를 참고하세요. 운영 모드에서는 개발 fixture 실행 route가 등록되지 않습니다.

## Docker 실행

저장소 루트에서:

```sh
docker compose up --build
```

- API: http://localhost:8000
- Swagger: http://localhost:8000/docs
- DB 연결 상태: http://localhost:8000/health
- PostgreSQL: `localhost:5432`, DB `terraform_intelligence`, 개발 계정 `postgres/postgres`

API 시작 시 `alembic upgrade head`를 실행합니다. Checkov는 API 컨테이너의 `/opt/checkov` 가상환경에 설치됩니다. 컨테이너는 일반 사용자, 읽기 전용 루트 파일시스템, `/tmp` tmpfs, 메모리·PID 제한으로 실행됩니다. 데이터는 `postgres_data` volume에 보존됩니다. API/DB 포트는 loopback에만 바인딩합니다.

```sh
docker compose logs -f api
docker compose exec api alembic current
docker compose stop
```

## 로컬 설치 및 Checkov

Python 3.12+, Git, PostgreSQL이 필요합니다. 로컬 개발에서 PostgreSQL만 시작하려면 루트에서 `docker compose up -d postgres`를 실행합니다.

```sh
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'

python3.12 -m venv .venv-checkov
.venv-checkov/bin/pip install -r requirements-scanner.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

**Checkov를 API와 같은 가상환경에 설치하지 마세요.** Checkov의 `bc-python-hcl2`와 `python-hcl2`가 모두 `hcl2` 패키지를 제공하므로 파일이 충돌합니다. 두 환경을 분리하고 `CHECKOV_BINARY=.venv-checkov/bin/checkov`를 설정합니다. 컨테이너도 같은 방식으로 분리합니다. 주요 의존성 버전은 검증한 버전으로 고정했습니다.

`.env`는 실행 디렉터리에서 읽으므로 로컬 명령은 `backend/`에서 실행합니다. 실제 스캔 중에는 `--reload`를 사용하지 않는 것이 좋습니다.

## 환경변수

| 변수 | 기본값 / 용도 |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/terraform_intelligence` |
| `WORKSPACE_ROOT` | `/tmp/terraform-intelligence` |
| `KEEP_SCAN_WORKSPACE` | `false`; 개발 확인용으로만 `true` |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `SESSION_SECRET` | 없음; 모두 있어야 GitHub 로그인이 켜짐 |
| `FRONTEND_URL`, `API_PUBLIC_URL` | `http://localhost:3000`, `http://localhost:8000`; 로그인 후 이동 주소와 OAuth callback, `Secure` 쿠키 판단 |
| `SESSION_TTL_DAYS` | `30`; 쿠키 Max-Age와 DB 만료가 같은 값 |
| `UPLOAD_ROOT` | `/tmp/terraform-intelligence-uploads`; 준비된 업로드의 임시 보관소(Compose에서는 `/tmp` tmpfs) |
| `UPLOAD_TTL_MINUTES` | `30` |
| `MAX_UPLOAD_ZIP_MB`, `MAX_UPLOAD_UNCOMPRESSED_MB`, `MAX_UPLOAD_FILES` | `10`, `50`, `1000` |
| `UPLOAD_PREFLIGHTS_PER_HOUR`, `MAX_RUNNING_PUBLISHES` | `20`, `3`; 사용자당 |
| `SECRET_SCAN_TIMEOUT_SECONDS` | `60` |
| `SNAPSHOT_ROOT` | `/tmp/terraform-intelligence-snapshots`; Compose는 `/data/snapshots` 볼륨 |
| `MAX_SNAPSHOT_SIZE_MB` | `50`, 압축 전 기준. 넘으면 스냅샷만 건너뜀 |
| `CHECKOV_BINARY` | `checkov`; 로컬 `.env.example`은 분리한 venv 경로 사용 |
| `CHECKOV_TIMEOUT_SECONDS` | `300` |
| `GIT_TIMEOUT_SECONDS` | `120` (각 Git 명령) |
| `MAX_REPOSITORY_SIZE_MB` | `200`, `.git` 포함 |
| `MAX_PROCESS_OUTPUT_MB` | `50`, stdout/stderr 합계 |
| `SCAN_WORKERS` | `2` |
| `SCAN_QUEUE_SIZE` | `20` |
| `LOG_LEVEL` | `INFO` |
| `CORS_ORIGINS` | JSON 배열 `["http://localhost:3000"]` |

컨테이너 설정은 루트 `docker-compose.yml`의 `environment`가 기준이며 `backend/.env`를 자동으로 읽어 들이지 않습니다. 프론트 클라이언트의 주소는 `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`입니다.

## API 계약

모든 ID는 UUID입니다. 목록은 JSON 배열이며 `limit` 기본 20/최대 100, `offset` 기본 0입니다. 목록은 생성 시각 내림차순이고 같은 시각은 ID로 정렬합니다.

| 메서드 | 경로 | 기능 / query |
|---|---|---|
| POST | `/api/scans` | 개발 모드 전용 바로 검사. `{ "source": "https://github.com/org/repo" }`, `202 Accepted` |
| GET | `/api/scans/{scan_id}` | module, repository, commit, summary, status, warnings, duration |
| GET | `/api/scans/{scan_id}/findings` | `severity`, `rule_id`, `resource_type`, `scope`, `limit`, `offset` |
| GET | `/api/repositories` | 탐색: `search`, `provider`, `sort`, `limit`(최대 50), `offset` |
| GET | `/api/repositories/{owner}/{name}` | 모듈 페이지(`?version=v1`) |
| GET | `/api/me/home` | 내 홈: 내 모듈, 확인이 필요한 모듈, 최근 활동 5개, 위험도 분포 |

사이드바 시절의 `GET /api/scans`(목록), `GET /api/modules`, `GET /api/modules/{id}`, `GET /api/dashboard`는 화면과 함께 없앴습니다. 올리기·가져오기·별표·사용하기 API는 루트 README를 봅니다.

```sh
curl -sS -X POST http://localhost:8000/api/scans \
  -H 'Content-Type: application/json' \
  -d '{"source":"https://github.com/terraform-aws-modules/terraform-aws-vpc"}'
# {"scan_id":"<uuid>","status":"QUEUED"}
# Location: /api/scans/<uuid>

SCAN_ID='<응답의 scan_id>'
curl -sS "http://localhost:8000/api/scans/$SCAN_ID"
curl -sS "http://localhost:8000/api/scans/$SCAN_ID/findings?severity=HIGH&limit=20&offset=0"
curl -sS 'http://localhost:8000/api/repositories?search=vpc'
```

프론트는 `COMPLETED` 또는 `FAILED`까지 상세 API를 주기적으로 조회할 수 있습니다. `202` 응답은 큐에 수락되었다는 뜻이며, 실행 상태가 더 빨리 바뀔 수도 있습니다. 실패 원인은 상세 응답의 `error_message`에 기록됩니다. 보안 위반을 찾은 스캔도 정상 실행이면 `COMPLETED`입니다.

```ts
import { api } from "@/lib/api";
const { scan_id } = await api.createScan("https://github.com/org/repo");
const scan = await api.scan(scan_id);
const findings = await api.findings(scan_id, { severity: "HIGH", limit: 20 });
```

Domain 오류는 `{ "error": { "code": "UnsupportedSourceError", "message": "..." } }` 형식입니다. 요청 schema/query 검증 오류는 FastAPI 기본 `422 detail` 형식입니다. 잘못된 URL 422, 없는 UUID 404, Registry 미지원 501, 큐 포화 503을 반환합니다. 큐 포화 시 이미 생성한 scan은 FAILED로 기록하고, 클라이언트는 나중에 새로운 스캔을 요청합니다.

Registry 문자열 `terraform-aws-modules/vpc/aws`는 식별하지만 실제 다운로드는 구현하지 않았습니다. GitHub 원본 URL을 사용하세요. Private repository, SSH, branch/tag 지정 및 인증 토큰은 지원하지 않습니다.

## DB schema / migration

- **modules**: UUID PK, source type/URL, owner/name/default branch, nullable provider/version, created/updated 시각. `(source_type, source_url)` UNIQUE와 savepoint로 동시 요청에서도 재사용합니다. `.git`/후행 slash/대소문자를 정규화합니다.
- **scans**: UUID PK, module FK, 상태, scanner, 실제 검사 commit/branch, 시간·소요 시간, severity별 개수(UNKNOWN 포함), 오류, partial flag/warnings, 원본 `raw_result JSONB`.
- **findings**: UUID PK, scan FK, rule/scanner/severity, title/resource/type, 상대 파일 경로·줄 범위, 설명/guideline, check result, `raw_data JSONB`.
- FK는 cascade delete, enum은 CHECK constraint. scan의 module/status/created, finding의 scan/severity/rule/resource type/created 및 `(scan_id,severity)`에 인덱스가 있습니다.
- Module의 최신 commit은 별도 중복 저장하지 않고 최신 Scan에서 조회합니다. branch는 module과 scan에 보존합니다. GitHub API·Registry 조회를 하지 않으므로 provider/version은 미확인 시 null입니다.
- Dashboard finding 수는 **완료된 모든 스캔의 누적 관측 수**입니다. 동일 저장소를 다시 스캔하면 중복 관측이 포함됩니다. partial 결과의 유효 finding도 포함됩니다.

```sh
cd backend
alembic upgrade head
alembic current
# 새 모델 변경 시
alembic revision --autogenerate -m 'describe change'
```

마이그레이션 파일은 `alembic/versions/0001_initial.py`입니다. 애플리케이션 시작 코드가 임의로 테이블을 생성하지 않습니다. SQLite는 테스트용 호환 경로이며 서비스 DB는 PostgreSQL 기준입니다.

## Checkov 실행 및 결과 의미

```text
QUEUED → COLLECTING → SCANNING → NORMALIZING → COMPLETED
                    어느 단계든 오류 발생 → FAILED
```

각 스캔은 `<WORKSPACE_ROOT>/<scan_uuid>/module`에 shallow clone 합니다. Terraform 후보는 `*.tf`/`*.tf.json`이며 `.git`, `.terraform`, `.external_modules`, `node_modules`, `vendor`를 제외합니다. 기존 HCL 경계 검증은 Checkov의 안전한 실행 여부를 결정합니다. 이후 독립 parser가 root와 호출된 local module의 semantic entity/graph를 만들고 Source Trust를 분석합니다. 단계별 실패 정책은 [2차 문서](docs/static-analysis.md#단계별-상태와-호환성)를 참고하세요.

대표 CLI:

```sh
checkov -d /workspace/module --framework terraform -o json --quiet --compact \
  --download-external-modules false --evaluate-variables false --skip-download \
  --config-file /workspace/checkov-config.yml \
  --skip-path '(^|/)(\.git|\.terraform|node_modules|vendor|\.external_modules)(/|$)'
```

Checkov가 `--config-file` 외에도 기본 설정 파일을 병합하므로 저장소 루트의 `.checkov.yml`/`.checkov.yaml`은 실행 중 별도 디렉터리로 격리하고 finally에서 복원합니다. 직접 생성한 빈 설정 파일과 통제된 HOME/cwd를 사용해 저장소 또는 사용자 설정이 외부 check 코드를 불러오지 못하게 합니다. subprocess에는 DB/AWS/GitHub credential 환경변수를 전달하지 않습니다. Git은 depth 1, HTTPS-only, redirect/전역 설정/hook/대화형 인증 비활성 상태로 실행합니다.

`--skip-download`는 Prisma 데이터 다운로드도 생략하므로 severity/guideline이 없을 수 있습니다. 이 경우 severity는 `UNKNOWN`이며 임의로 HIGH 등의 값을 만들지 않습니다. 추가 rule별 severity는 `SeverityMapper`에 주입할 수 있습니다. 옵션 의미는 [Checkov CLI 문서](https://www.checkov.io/2.Basics/CLI%20Command%20Reference.html)를 기준으로 합니다.

- 종료 코드 0/1은 검사 완료 가능(1은 위반 발견), 그 외는 실행 오류입니다.
- FAILED check만 Finding으로 저장합니다. passed/skipped check는 finding 수에 포함하지 않습니다.
- 유효한 empty report는 0 findings, 빈 stdout/잘못된 JSON/잘못된 report 구조는 FAILED입니다.
- Checkov parsing error나 일부 잘못된 finding은 유효 결과를 보존하면서 `partial_result=true` 및 warnings를 표시합니다. 검사가 전부 성공한 것으로 해석하면 안 됩니다.
- 스냅샷을 저장하지 못하거나 버전을 기록하지 못하면 검사는 `FAILED`입니다.
- 검사가 끝나면 수집한 소스를 `<SNAPSHOT_ROOT>/<sha256>.tar.gz`로 보관하고(`.git`, `.terraform`, `*.tfstate*`, `*.tfvars`, `.env*`, `*.pem`, `*.key`, `id_rsa*`, `*.p12` 제외, 같은 내용은 한 번만 저장) root 모듈의 variable/output, README, 라이선스를 `module_versions`에 기록합니다. 이 단계의 실패는 로그만 남기며 검사 결과에 영향을 주지 않습니다.
- 정상·실패 모두 workspace context의 finally에서 정리합니다. `KEEP_SCAN_WORKSPACE=true`인 개발 설정에서는 남깁니다.
- raw 결과에는 소스 관련 민감 정보가 있을 수 있으므로 DB 접근 권한으로 관리해야 합니다. 원문과 subprocess stderr는 로그에 기록하지 않습니다.

## 테스트

```sh
cd backend
source .venv/bin/activate
pytest -q                         # Checkov가 설치되면 실제 fixture 검사 포함
pytest -q -m 'not checkov and not postgres'  # 외부 도구/DB 없는 CI
pytest -q -m checkov               # 실제 Checkov: S3, 공개 SSH, 공개 RDS
ruff check .
ruff format --check .
```

실제 Checkov 검사는 `CHECKOV_BINARY`, `backend/.venv-checkov/bin/checkov`, PATH 순으로 실행 파일을 찾으며 없으면 skip합니다. subprocess mock 테스트는 항상 실행합니다. API/파이프라인 테스트는 임시 SQLite와 fixture collector/scanner를 사용하며 외부 GitHub에 의존하지 않습니다. timeout/output limit/disk limit 테스트는 안전한 로컬 subprocess를 실제 실행합니다.

실제 PostgreSQL 테스트는 실행 중인 개발/테스트 DB에서:

```sh
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/terraform_intelligence \
  pytest -q -m postgres
```

이 테스트는 고유한 임시 schema를 만들고 종료 시 해당 schema만 삭제합니다. migration/model 일치, JSONB round-trip, 동시 Module 생성 중복 방지를 확인합니다. DB 계정에 schema 생성 권한이 필요합니다.

## 확장 위치 및 현재 제한

현재 **module dependency graph**, 규칙 기반 **source trust analysis**, **정적 cross-module data flow**를 구현했습니다. 근거 기반 **Risk Correlation**도 구현했습니다. 전체 **trust scoring**, **malicious module behavior analysis**는 아직 구현하지 않습니다.

기존 `app/graphs/builder.py`의 `DependencyGraphBuilder`와 Entity/Edge를 기반으로 `analyzers/data_flow.py`가 flow를 생성하며, 향후 `supply_chain.py` 등에 새 `ModuleAnalyzer`를 추가할 수 있습니다. 수집기는 `SourceCollector`, scanner는 `SecurityScanner`, 결과 변환기는 `FindingNormalizer`를 구현합니다. Flow는 관찰 graph로 유지하고 보안 분석 결과는 표준 Finding으로 합칠 수 있으며 `correlation/`의 Engine과 Rule이 ScanService에 연결되어 있습니다. 외부 worker는 `ScanDispatcher`를 교체하고 `ScanService.run(scan_id)`를 호출하도록 구성합니다.

현재 제한:

- in-process 작업 대기열은 영속 queue가 아닙니다. 정상 종료는 실행 작업을 기다리지만 강제 종료/재시작 시 QUEUED 또는 실행 중 상태가 남을 수 있습니다. 자동 재시도·중단 작업 복구·취소 API는 없습니다. 해당 기록은 이력으로 유지하고 새 스캔을 요청하세요. 운영 단계에서는 durable queue, lease/heartbeat, recovery를 추가해야 합니다.
- 외부 module 다운로드와 변수 평가를 비활성화하므로 외부 module 내부 및 평가가 필요한 일부 규칙은 검사 범위에서 빠집니다. inline suppression은 Checkov 정책을 따릅니다. 이 스캔의 0 findings는 안전성 보증이 아닙니다.
- GitHub 공개 저장소의 default branch만 검사합니다. GitHub URL은 정확히 `https://github.com/owner/repo` 형식만 지원합니다. symlink가 있는 검사 대상 트리는 거부합니다.
- 크기/출력 제한은 실행 중 polling과 종료 후 검사라 짧은 순간 초과할 수 있습니다. Docker tmpfs/메모리/PID 제한도 함께 사용합니다. Checkov를 별도 가상환경에서 실행하는 것은 OS 수준의 격리가 아닙니다. 다중 사용자 공개 서비스에서는 scanner 전용 컨테이너·네트워크 제한·인증·rate limit이 추가로 필요합니다.
- API는 로컬 개발 MVP이며 인증·사용자/조직별 접근 제어를 구현하지 않았습니다. 프론트엔드는 실제 분석 API와 연결되어 있으며 개발 fixture UI만 별도 설정으로 노출됩니다.

## 3차: Terraform Data Flow

기존 Entity/GraphEdge 기반의 attribute-level flow, local module 입출력 연결, opaque external boundary, source/sink 분류와 flow/trace API를 지원합니다. Flow 관찰만으로 Finding을 생성하지 않습니다.

[Data Flow 설계·지원 범위·API·검증 방법](docs/data-flow-analysis.md)을 참고하세요. DB migration은 `0003`이며 Docker 시작 시 적용됩니다.

## 4차: Risk Correlation

명시적인 flow 또는 provider dependency 근거가 있는 TFCR001–TFCR008을 지원합니다. 원본 Finding과 edge UUID를 evidence에 보존하며 scanner=correlation 필터·상세 attack path API·dashboard breakdown을 제공합니다. 새 DB migration은 없습니다.

[규칙·매핑·confidence·false-positive 억제·API·제한사항](docs/risk-correlation.md)을 참고하세요.
