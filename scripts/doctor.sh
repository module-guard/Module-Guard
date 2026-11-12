#!/usr/bin/env bash
# 환경 사전 점검 (issue #51). 코드가 아니라 "이 PC의 환경"만 본다.
# 실패하면 [환경 문제] 줄과 해결 명령을 출력하고 1로 끝난다. 테스트 실패와 구분하기 위한 것이다.
# VERIFY_E2E=1 이면 Docker와 E2E 포트(3100, 8001)도 점검한다. docker-compose.e2e.yml 은 DB 포트를 호스트에 열지 않는다.
cd "$(dirname "$0")/.." || exit 2

PY="${BACKEND_PYTHON:-backend/.venv/bin/python}"
bad=0
fail() { printf '[환경 문제] %s\n    해결: %s\n' "$1" "$2"; bad=1; }
ok() { printf '[ok] %s\n' "$1"; }

# 1. 백엔드 venv와 Python 3.12 (CI와 backend/Dockerfile의 버전)
if [ ! -x "$PY" ]; then
  fail "백엔드 venv가 없습니다 ($PY)" "cd backend && python3.12 -m venv .venv && .venv/bin/pip install -e '.[test]'"
else
  v=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
  [ "$v" = 3.12 ] && ok "백엔드 Python $v" ||
    fail "백엔드 Python이 $v 입니다 (3.12 필요)" "rm -rf backend/.venv && cd backend && python3.12 -m venv .venv && .venv/bin/pip install -e '.[test]'"
  # 2. 앱·테스트 의존성이 없으면 아래 hcl2 점검은 의미가 없다 (빈 venv를 충돌로 오인하지 않게)
  # 3. Checkov의 bc-python-hcl2가 앱 venv에 섞이면 hcl2.utils가 사라진다 (backend/README.md)
  if ! "$PY" -c 'import fastapi, pytest, hcl2' 2>/dev/null; then
    fail "앱·테스트 의존성이 설치되어 있지 않습니다 ($PY)" "cd backend && .venv/bin/pip install -e '.[test]'"
  elif "$PY" -c 'import hcl2.utils' 2>/dev/null; then
    ok "hcl2 충돌 없음"
  else
    fail "앱 venv의 hcl2 패키지가 깨졌습니다 (Checkov의 bc-python-hcl2와 python-hcl2 충돌: No module named 'hcl2.utils')" \
      "cd backend && rm -rf .venv && python3.12 -m venv .venv && .venv/bin/pip install -e '.[test]'   # Checkov는 .venv-checkov에만 설치"
  fi
fi

# 4. 별도 Checkov venv (CHECKOV_BINARY 로 바꿀 수 있다)
CK="${CHECKOV_BINARY:-backend/.venv-checkov/bin/checkov}"
[ -x "$CK" ] && ok "Checkov: $CK" ||
  fail "Checkov 실행 파일이 없습니다 ($CK)" "cd backend && python3.12 -m venv .venv-checkov && .venv-checkov/bin/pip install -r requirements-scanner.txt"

# 5. Node 의존성과 Playwright 브라우저 (설치된 @playwright/test가 기대하는 Chromium 경로)
if [ ! -d node_modules ]; then
  fail "node_modules가 없습니다" "npm ci"
else
  ok "node_modules"
  if [ "$(node -e 'console.log(require("fs").existsSync(require("@playwright/test").chromium.executablePath()))' 2>/dev/null)" = true ]; then
    ok "Playwright Chromium"
  else
    fail "Playwright Chromium이 설치된 @playwright/test 버전과 맞지 않거나 없습니다" "npx playwright install chromium"
  fi
fi

# 6. E2E에만 필요한 것: Docker와 포트
if [ "${VERIFY_E2E:-}" = 1 ]; then
  docker info >/dev/null 2>&1 && ok "Docker" || fail "Docker 데몬에 연결할 수 없습니다" "Docker Desktop을 실행하세요"
  for p in 3100 8001; do
    if lsof -nP -iTCP:"$p" -sTCP:LISTEN >/dev/null 2>&1; then
      fail "포트 $p 가 이미 사용 중입니다" "lsof -nP -iTCP:$p -sTCP:LISTEN 으로 어떤 프로세스인지 확인하세요. 내 것이 아니면 끄지 말고 그쪽 포트가 비는 때에 다시 실행하세요"
    fi
  done
fi

[ "$bad" = 0 ] && echo "환경 점검 통과" || echo "환경 점검 실패: 위 [환경 문제]를 먼저 해결하세요. 이것은 테스트 실패가 아닙니다."
exit "$bad"
