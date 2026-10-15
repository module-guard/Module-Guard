[공통 규칙 — 반드시 지킬 것]
이 저장소는 Module-Guard(Terraform 모듈 보안 분석 플랫폼)다.
백엔드는 backend/ (FastAPI, SQLAlchemy, PostgreSQL, Alembic), 프론트는 저장소 루트 (Next.js, TypeScript, openapi-fetch)다.
팀원 5명이 동시에 작업하므로 아래 규칙을 어기면 머지 충돌이 난다.

1. 파일 소유권: 내 역할 프롬프트의 [내 담당 파일]에 있는 파일만 수정하거나 새로 만든다.
   그 밖의 파일은 읽기만 한다. 다른 담당자 파일을 고쳐야 할 것 같으면 고치지 말고,
   작업 끝에 "다른 담당자에게 요청할 변경" 목록으로 보고한다.
2. 아무도 수정하지 않는 파일:
   backend/app/main.py, backend/app/models/**, backend/alembic/** (DB 마이그레이션 금지, head는 0009 유지),
   backend/app/schemas/analysis.py, backend/app/correlation/**, backend/tests/conftest.py,
   backend/README.md, docker-compose*.yml, Dockerfile, backend/Dockerfile,
   backend/requirements*.txt, backend/pyproject.toml.
3. 생성 파일: openapi.json, src/api/generated/** 는 프론트 담당만 커밋한다.
   백엔드 담당은 확인용으로 생성했더라도 커밋 전에 되돌린다.
4. API 변경은 추가만 한다. 기존 경로·필드·쿼리 이름을 지우거나 바꾸지 않는다.
   새 필드에는 기본값을 준다. 프롬프트의 [API 계약]은 이름·타입을 그대로 따른다
   (프론트가 이 계약으로 먼저 개발한다). 바꿔야 하면 구현 전에 팀에 알린다.
5. 테스트와 fixture는 내 담당으로 적힌 테스트 파일·디렉터리나 새 파일에만 추가한다.
6. 문서는 backend/docs/ 아래 내 담당 문서 파일만 수정한다.
7. 보안 규칙 유지: terraform init/plan/apply 실행 금지, subprocess는 app/utils/subprocess.py의
   ProcessRunner만 사용, shell=True 금지, 외부 도구의 오류 원문(stderr)을 응답·로그에 노출하지 않는다.
8. 끝내기 전 확인 (backend/에서): pytest -q -m 'not checkov and not postgres',
   ruff check ., ruff format --check . 가 모두 통과해야 한다.
   기존 테스트를 지우거나 기대값을 느슨하게 바꾸지 않는다. 기대값을 바꿔야 하면 이유를 보고에 적는다.
9. 커밋은 내 브랜치에만 하고 main에 직접 push하지 않는다. PR 전에 main으로 rebase한다.
10. 작업이 끝나면 보고한다: 바뀜 파일 목록, 추가한 API와 응답 예시 JSON, 테스트 결과,
    다른 담당자에게 요청할 변경.
