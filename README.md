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