#!/usr/bin/env bash
# Read-only health check: is this verify run worth driving?
# Usage: doctor.sh [run-id]   (default: .verify-moldy/current). Exit 0 = healthy.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

RUN_DIR="$(resolve_run_dir "${1:-}")" || exit 2
load_state "$RUN_DIR" || exit 2

fail=0
check() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    printf '  ok    %s\n' "$label"
  else
    printf '  FAIL  %s\n' "$label"
    fail=1
  fi
}
port_owned_by_group() {
  # the listener on $1 must belong to process group $2 (a run we started)
  local pid
  for pid in $(lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null); do
    [[ "$(ps -o pgid= -p "$pid" | tr -d ' ')" == "$2" ]] && return 0
  done
  return 1
}
login_ok() {
  curl -fsS -X POST "$API_URL/api/auth/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$USER_EMAIL\",\"password\":\"$USER_PASSWORD\"}"
}
scripted_model_seeded() {
  local jar
  jar="$RUN_DIR/.doctor-cookies"
  curl -fsS -c "$jar" -X POST "$API_URL/api/auth/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$USER_EMAIL\",\"password\":\"$USER_PASSWORD\"}" >/dev/null &&
    curl -fsS -b "$jar" "$API_URL/api/models" | grep -q '"document-artifact-scripted"'
  local rc=$?
  rm -f "$jar"
  return $rc
}

echo "verify-moldy doctor: run $RUN_ID (rev $GIT_REV)"
if [[ -f "$RUN_DIR/CLEANED" ]]; then
  echo "  FAIL  run was already cleaned up — start a new one with up.sh"
  exit 1
fi
check "postgres cluster $PG_DATA running" "$PG_BIN/pg_ctl" -D "$PG_DATA" status
check "postgres accepting on 127.0.0.1:$PG_PORT" "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PG_PORT"
check "backend process group $BACKEND_PGID alive" kill -0 "$BACKEND_PGID"
check "backend port $BACKEND_PORT owned by our process" port_owned_by_group "$BACKEND_PORT" "$BACKEND_PGID"
check "GET $API_URL/api/health" curl -fsS "$API_URL/api/health"
check "seed user can log in" login_ok
check "scripted model seeded" scripted_model_seeded
check "frontend process group $FRONTEND_PGID alive" kill -0 "$FRONTEND_PGID"
check "frontend port $FRONTEND_PORT owned by our process" port_owned_by_group "$FRONTEND_PORT" "$FRONTEND_PGID"
check "GET $APP_URL/login" curl -fsS -o /dev/null "$APP_URL/login"
check "repo HEAD still $GIT_REV" bash -c "[[ \$(git -C '$VERIFY_REPO' rev-parse --short HEAD) == '$GIT_REV' ]]"

if ((fail)); then
  echo "unhealthy — see $RUN_DIR/logs/*.log"
  exit 1
fi
echo "healthy: $APP_URL"
