.PHONY: api-sync api-check doctor verify verify-full
api-sync:
	npm run api:sync
api-check:
	npm run api:check

# 환경 사전 점검 (#51). 실패하면 테스트가 아니라 환경 문제다.
doctor:
	@bash scripts/doctor.sh

# ci.yml 과 같은 순서 (#51). VERIFY_E2E=1 이면 Playwright(격리 스택)까지.
# api:check 는 openapi/생성 파일 비교에 이어 tsc --noEmit 과 Vitest 도 실행한다.
BPY := $(or $(BACKEND_PYTHON),$(CURDIR)/backend/.venv/bin/python)
verify: doctor
	@echo "== 1 ruff check";  cd backend && $(BPY) -m ruff check .
	@echo "== 2 ruff format"; cd backend && $(BPY) -m ruff format --check .
	@echo "== 3 pytest";      cd backend && $(BPY) -m pytest -q -m 'not checkov and not postgres'
	@echo "== 4 api:check (+ typecheck, vitest)"; BACKEND_PYTHON=$(BPY) npm run api:check
	@if [ "$(VERIFY_FULL)" = 1 ]; then $(MAKE) --no-print-directory verify-full || exit 1; fi
	@if [ "$(VERIFY_E2E)" = 1 ]; then echo "== e2e (terraform 은 PATH 에서 가린다: AGENTS.md 7번, CI 의 no-terraform 과 같음)"; \
	  d=$$(mktemp -d) && printf '#!/bin/sh\nexit 127\n' > $$d/terraform && chmod +x $$d/terraform && PATH="$$d:$$PATH" npx playwright test; r=$$?; rm -rf $$d; exit $$r; fi
	@echo "verify 통과"

# VERIFY_FULL=1: CI 의 "Real Checkov scanner" 와 "PostgreSQL and migrations" 작업 (#51).
# TEST_DATABASE_URL 은 일회용 DB 여야 한다: downgrade base 와 테스트가 스키마를 지운다.
CK := $(or $(CHECKOV_BINARY),$(CURDIR)/backend/.venv-checkov/bin/checkov)
verify-full:
	@db=$${TEST_DATABASE_URL##*/}; db=$${db%%\?*}; case "$$db" in \
	  ci|*test*) ;; \
	  *) echo "[환경 문제] VERIFY_FULL=1 에는 일회용 PostgreSQL 의 TEST_DATABASE_URL 이 필요합니다 (DB 이름이 ci 이거나 test 를 포함해야 함). 테스트 실패가 아닙니다."; exit 1;; esac
	@echo "== checkov tests"; t=$$(mktemp) && cd backend && CHECKOV_BINARY=$(CK) $(BPY) -m pytest -q -m checkov --junitxml=$$t && python3 ../.github/scripts/require-tests-ran.py $$t 5
	@echo "== alembic upgrade/downgrade/upgrade"; cd backend && export DATABASE_URL="$$TEST_DATABASE_URL" && A=$(dir $(BPY))alembic && $$A upgrade head && $$A downgrade base && $$A upgrade head && $$A current | grep -q '(head)' && $$A downgrade base
	@echo "== postgres tests"; t=$$(mktemp) && cd backend && $(BPY) -m pytest -q -m postgres --junitxml=$$t && python3 ../.github/scripts/require-tests-ran.py $$t 2
