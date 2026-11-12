# ModuleGuard — Terraform Module Intelligence

기존 한국어 Next.js UI에서 FastAPI/PostgreSQL의 Checkov, Source Trust, Dependency Graph, Data Flow, Risk Correlation 결과를 조회합니다. 일반 화면은 실제 API 데이터를 사용합니다. Terraform 실행과 외부 모듈 재귀 다운로드는 수행하지 않습니다.

## UI 테스트 방법

```sh
docker compose up --build
```

1. http://localhost:3000 (랜딩) → **시작하기** → `/explore`에서 모듈을 찾습니다(미러 모듈은 `미러` 표시).
2. 모듈 페이지(`/@주인/이름`)에서 `개요` · `사용하기` · `입력·출력` · `보안` · `버전` 탭을 봅니다. `보안`의 **검사 상세**는 요약 / 보안 탐지 / 공급망 신뢰 / 의존성 / 데이터 흐름 / 상관 분석 탭입니다.
3. GitHub로 로그인하면 `/home`(내 홈)으로 갑니다. 내 모듈, 확인이 필요한 모듈(검사 실패, 90일 넘은 검사), 최근 활동, 위험도 분포가 있습니다.
4. **올리기**(`/new`)에서 GitHub 주소나 zip을 올리고, 모듈 페이지에서 별표·가져오기·사용하기를 확인합니다.

사이드바 시절 주소(`/dashboard`, `/history` → `/home`, `/modules`, `/trusted` → `/explore`)는 발표 전까지 임시(307) 리다이렉트입니다. `/scans/{id}`는 그 검사의 모듈 검사 화면으로 옮겨 가고, 모듈에 속하지 않은 개발용 검사만 개발 모드에서 그 자리에 보입니다.

Frontend: http://localhost:3000 · Backend: http://localhost:8000 · Swagger: http://localhost:8000/docs

## 처음 설정 → 전체 검증

```sh
cd backend
python3.12 -m venv .venv && .venv/bin/pip install -e '.[test]'
python3.12 -m venv .venv-checkov && .venv-checkov/bin/pip install -r requirements-scanner.txt   # Checkov는 별도 venv (hcl2 충돌 방지)
cd ..
npm ci
npx playwright install chromium    # E2E를 돌릴 때만
make doctor                        # 환경 점검: 문제가 있으면 [환경 문제]와 해결 명령을 출력
make verify                        # ruff → pytest → api:check(+tsc, Vitest). VERIFY_FULL=1 이면 Checkov·PostgreSQL 마커, VERIFY_E2E=1 이면 Playwright까지
```

`[환경 문제]`로 끝나면 코드가 아니라 이 PC의 환경 문제입니다. 테스트 실패는 `verify`의 `== 번호` 단계 아래에 pytest/tsc/Playwright 출력으로 나옵니다. `VERIFY_E2E=1`은 Docker와 포트 3100·8001이 비어 있어야 합니다(E2E의 DB는 호스트 포트를 열지 않으므로 5432는 상관없습니다). `VERIFY_FULL=1`은 **일회용** PostgreSQL이 필요합니다. 예: `docker run -d --name mg-test-pg -e POSTGRES_USER=ci -e POSTGRES_PASSWORD=ci -e POSTGRES_DB=ci -p 127.0.0.1:55432:5432 postgres:16-alpine` 후 `TEST_DATABASE_URL=postgresql+psycopg://ci:ci@127.0.0.1:55432/ci make verify VERIFY_FULL=1`. 개발 DB를 가리키면 안 됩니다(`downgrade base`와 테스트가 스키마를 지웁니다). DB 이름이 `ci`이거나 `test`를 포함하지 않으면 `verify-full`이 거부합니다.

| 도구 | 버전 |
|---|---|
| Python | 3.12 (CI·`backend/Dockerfile`과 같음) |
| Node | 22 |
| Docker | E2E에만 필요 (`docker compose`) |
| Chromium | `npx playwright install chromium` (설치된 `@playwright/test`가 기대하는 버전) |

| pytest 마커 | 의미 | 건너뛰는 조건 |
|---|---|---|
| `checkov` | 실제 Checkov 실행 | `CHECKOV_BINARY`가 없으면 건너뜀. 기본 실행(`not checkov`)에서는 제외 |
| `postgres` | 일회용 PostgreSQL 필요 | `TEST_DATABASE_URL`이 없으면 건너뜀. 기본 실행에서는 제외 |

E2E의 `RUN_LIVE_SCAN`(실제 GitHub·Registry 수집), `RUN_DEMO`(시연 시나리오)는 설정하지 않으면 해당 spec이 건너뜁니다. `use.spec.ts`의 terraform 단계는 로컬에 `terraform`이 없으면 건너뜁니다. 실제 수집 시나리오는 아래 '외부 네트워크가 필요한 시나리오 (수동 실행)'을 보세요.

| 로컬 | CI 작업 (`ci.yml`) |
|---|---|
| `make verify` 1~3단계 | Backend lint and tests |
| `make verify` 4단계 (`npm run api:check`) | API contract, typecheck and unit tests |
| `make verify VERIFY_E2E=1`의 `== e2e` | Browser tests (isolated stack) |
| `make verify VERIFY_FULL=1` (`verify-full`) | Real Checkov scanner, PostgreSQL and migrations (기본 `verify`에는 없음) |

- CI의 e2e는 스택을 먼저 띄우고 `--reporter=list,html`로 돌리지만, 로컬은 `playwright.config.ts`가 같은 스택(`module-guard-e2e`)을 직접 띄웁니다.
- CI는 `no-terraform` 액션으로 terraform을 지웁니다. 로컬 `VERIFY_E2E=1`도 실행하는 동안 PATH에서 terraform을 가려서 `use.spec.ts`의 terraform 단계가 건너뛰어집니다(AGENTS.md 7번: terraform 실행 금지). 직접 `npx playwright test`를 돌릴 때는 로컬에 terraform이 있으면 그 단계가 실제로 실행되니 같은 방식으로 가리세요.

명령은 `ci.yml`이 바뀌면 `Makefile`의 `verify`도 같이 고칩니다.

## 자동 검증 (GitHub Actions)

PR을 올리거나 main에 push하면 `.github/workflows/ci.yml`이 아래 다섯 작업을 돌립니다. 하나라도 실패하면 PR에 실패로 표시됩니다. 운영 데이터와 실제 OAuth 비밀값은 쓰지 않고, GitHub 토큰은 읽기 권한만 씁니다.

| 작업 | 하는 일 | 로컬에서 같은 명령 |
|---|---|---|
| Backend lint and tests | Ruff 검사·포맷 확인, SQLite 테스트 | `backend/`에서 `ruff check .`, `ruff format --check .`, `pytest -q -m 'not checkov and not postgres'` |
| PostgreSQL and migrations | 일회용 PostgreSQL 16에서 Alembic upgrade → downgrade → upgrade, `postgres` 테스트 | `TEST_DATABASE_URL=... pytest -m postgres` |
| Real Checkov scanner | 별도 venv의 실제 Checkov로 `checkov` 테스트 | `CHECKOV_BINARY=... pytest -m checkov` |
| API contract, typecheck and unit tests | `openapi.json`·`src/api/generated`가 백엔드와 같은지 비교, `tsc --noEmit`, Vitest | `npm run api:check` |
| Browser tests (isolated stack) | `docker-compose.e2e.yml`(API·PostgreSQL)과 e2e 전용 프런트 빌드로 Playwright 실행 | `npx playwright test` |

- PostgreSQL·Checkov 테스트는 DB 주소나 Checkov가 없으면 스스로 건너뜁니다. CI에서는 건너뛰면 실패로 처리합니다(`.github/scripts/require-tests-ran.py`). 로그에 실제로 돈 테스트 이름이 남습니다.
- 브라우저 테스트가 실패하면 trace·스크린샷·영상과 API·프런트 로그를 `e2e-failure` 결과물로 14일 동안 보관합니다. 테스트 스택은 성공·실패와 상관없이 마지막에 내립니다.
- AGENTS.md 7번에 따라 CI 러너에서는 terraform을 지웁니다. terraform이 있어야 하는 테스트 단계는 건너뜁니다.

### 외부 네트워크가 필요한 시나리오 (수동 실행)

GitHub·Terraform Registry에 실제로 접속하는 시나리오는 기본 CI에 넣지 않았습니다. 시연 전이나 수집기·올리기·Checkov 처리를 바꾼 뒤에 **Actions → Live smoke → Run workflow**로 직접 돌립니다.

- `live`: `RUN_LIVE_SCAN=true`, 실제 공개 모듈 수집·미러 등록 (`mirror`, `platform`, `use` spec)
- `demo`: `RUN_DEMO=true`, PRODUCT_PLAN.md 시연 시나리오 1~7 (`demo` spec)

결과물(`live-smoke`)에는 성공해도 리포트와 로그가 남습니다.
