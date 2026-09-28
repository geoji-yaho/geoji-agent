#!/usr/bin/env bash
# 테스트 실행 진입점. 언제 무엇을 돌리는지와 보고 형식은 TESTING.md.
#
#   scripts/test.sh lint                 ruff check + format --check (고치지 않는다)
#   scripts/test.sh fast [pytest 인자]    통합 테스트를 뺀 pytest. DB·벤더 없음
#   scripts/test.sh integration [인자]    로컬 Postgres 를 띄우고 tests/integration
#   scripts/test.sh all                  lint → fast → integration. 하나가 실패해도 끝까지 돈다
#   scripts/test.sh path <대상...>        특정 파일·테스트만. tests/integration 이 섞이면 DB 를 띄운다
#
# 종료 코드: 0 통과, 1 테스트·린트 실패, 2 실행 불가(Docker 꺼짐, DB 기동 실패, 인자 오류 등)
set -uo pipefail
cd "$(dirname "$0")/.."

# 로컬 docker-compose.dev.yml 전용 값. 서비스의 DATABASE_URL(공유 Supabase)은 쓰지 않는다(testing 룰)
LOCAL_DB_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
PYTEST=(uv run pytest -q --tb=short -rfE)

# pytest 종료 코드 1 은 실패, 그 밖의 0 아닌 값(중단·수집 오류·사용 오류)은 실행 불가로 본다
pytest_rc() {
  "${PYTEST[@]}" "$@"
  local rc=$?
  if [[ $rc -eq 0 || $rc -eq 1 ]]; then return $rc; fi
  return 2
}

db_up() {
  # 사용자가 따로 준 테스트 DB 가 있으면 그것을 쓴다. 값은 비밀이라 출력하지 않는다
  if [[ -n "${TEST_DATABASE_URL:-}" ]]; then return 0; fi
  if ! docker info >/dev/null 2>&1; then
    echo "SETUP-FAIL: Docker 가 꺼져 있다. Docker Desktop 을 켜야 한다" >&2
    return 2
  fi
  if ! docker compose -f docker-compose.dev.yml up -d --wait postgres >/dev/null 2>&1; then
    echo "SETUP-FAIL: 로컬 Postgres(geoji-ai-postgres, 5432)를 띄우지 못했다. 'docker ps' 로 5432 포트 점유를 확인한다" >&2
    return 2
  fi
  export TEST_DATABASE_URL="$LOCAL_DB_URL"
}

# 개수는 여기서 센다. 러너가 ruff 출력을 읽고 세면 "272 files already formatted" 같은 줄을 위반 수로 잘못 읽는다
run_lint() {
  local check_out format_out check_rc format_rc check_n format_n
  check_out=$(uv run ruff check . --output-format concise 2>&1); check_rc=$?
  format_out=$(uv run ruff format --check --output-format concise . 2>&1); format_rc=$?
  check_n=$(grep -cE '^[^ ].*:[0-9]+:[0-9]+: [A-Z]+[0-9]+ ' <<<"$check_out")
  format_n=$(grep -c ': unformatted: ' <<<"$format_out")
  [[ $check_n -gt 0 ]] && grep -E '^[^ ].*:[0-9]+:[0-9]+: [A-Z]+[0-9]+ ' <<<"$check_out"
  [[ $format_n -gt 0 ]] && grep ': unformatted: ' <<<"$format_out"
  echo "LINT-SUMMARY: ruff_check_errors=$check_n unformatted_files=$format_n"
  # 위반을 못 셌는데 ruff 가 0 이 아니면 설정·실행 오류다
  if [[ $check_rc -ne 0 && $check_n -eq 0 ]] || [[ $format_rc -ne 0 && $format_n -eq 0 ]]; then
    printf '%s\n%s\n' "$check_out" "$format_out" >&2
    echo "SETUP-FAIL: ruff 가 위반 없이 실패했다" >&2
    return 2
  fi
  [[ $check_n -eq 0 && $format_n -eq 0 ]]
}

run_fast() { pytest_rc --ignore=tests/integration "$@"; }

run_integration() {
  db_up || return 2
  pytest_rc tests/integration "$@"
}

run_all() {
  local lint fast integ
  echo "=== lint ==="
  run_lint; lint=$?
  echo "=== fast ==="
  run_fast; fast=$?
  echo "=== integration ==="
  run_integration; integ=$?
  echo "=== summary: lint=$lint fast=$fast integration=$integ ==="
  local max=$lint
  (( fast > max )) && max=$fast
  (( integ > max )) && max=$integ
  return $max
}

run_path() {
  if [[ $# -eq 0 ]]; then
    echo "SETUP-FAIL: path 뒤에 테스트 파일이나 노드 ID 를 준다" >&2
    return 2
  fi
  if [[ "$*" == *tests/integration* ]]; then db_up || return 2; fi
  pytest_rc "$@"
}

suite="${1:-}"
shift || true
case "$suite" in
  lint) run_lint ;;
  fast) run_fast "$@" ;;
  integration) run_integration "$@" ;;
  all) run_all ;;
  path) run_path "$@" ;;
  *)
    echo "사용법: scripts/test.sh {lint|fast|integration|all|path <대상...>}" >&2
    exit 2
    ;;
esac
