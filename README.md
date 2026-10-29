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
