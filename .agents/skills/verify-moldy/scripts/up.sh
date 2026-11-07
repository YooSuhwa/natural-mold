#!/usr/bin/env bash
# Launch a throwaway Moldy stack (Postgres cluster + backend + frontend dev server)
# on free ports with its own DB, data dir, Next build dir and seeded super user.
# Usage: up.sh            -> prints RUN_ID and URLs, links .verify-moldy/current
set -Eeuo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

RUN_ID="${VERIFY_RUN_ID:-$(date +%Y%m%d-%H%M%S)-$RANDOM}"
RUN_DIR="$VERIFY_ROOT/runs/$RUN_ID"
mkdir -p "$VERIFY_ROOT/runs"
mkdir "$RUN_DIR"
mkdir -p "$RUN_DIR"/{data,logs,evidence}
: >"$RUN_DIR/state.env"
chmod 600 "$RUN_DIR/state.env"
ln -sfn "runs/$RUN_ID" "$VERIFY_ROOT/current"

on_fail() {
  echo "verify-moldy: launch failed. Logs: $RUN_DIR/logs. Run: $VERIFY_SKILL_DIR/scripts/down.sh $RUN_ID" >&2
}
trap on_fail ERR

BACKEND_PORT="$(free_port)"
FRONTEND_PORT="$(free_port)"
while [[ "$FRONTEND_PORT" == "$BACKEND_PORT" ]]; do FRONTEND_PORT="$(free_port)"; done
PG_BIN="${VERIFY_PG_BIN:-/opt/homebrew/opt/postgresql@16/bin}"
[[ -x "$PG_BIN/initdb" ]] || { echo "verify-moldy: no initdb in $PG_BIN (brew install postgresql@16, or set VERIFY_PG_BIN)" >&2; exit 1; }
PG_PORT="$(free_port)"
while [[ "$PG_PORT" == "$BACKEND_PORT" || "$PG_PORT" == "$FRONTEND_PORT" ]]; do PG_PORT="$(free_port)"; done
PG_DATA="$RUN_DIR/pg"
PG_PASSWORD="$(python3 -c 'import secrets;print(secrets.token_hex(16))')"
USER_EMAIL="verify-$RUN_ID@moldy.dev"
USER_PASSWORD="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
NEXT_DIST=".next-verify/$RUN_ID"

state_set "$RUN_DIR" RUN_ID "$RUN_ID"
state_set "$RUN_DIR" RUN_DIR "$RUN_DIR"
state_set "$RUN_DIR" PG_BIN "$PG_BIN"
state_set "$RUN_DIR" PG_DATA "$PG_DATA"
state_set "$RUN_DIR" PG_PORT "$PG_PORT"
state_set "$RUN_DIR" BACKEND_PORT "$BACKEND_PORT"
state_set "$RUN_DIR" FRONTEND_PORT "$FRONTEND_PORT"
state_set "$RUN_DIR" API_URL "http://localhost:$BACKEND_PORT"
state_set "$RUN_DIR" APP_URL "http://localhost:$FRONTEND_PORT"
state_set "$RUN_DIR" USER_EMAIL "$USER_EMAIL"
state_set "$RUN_DIR" USER_PASSWORD "$USER_PASSWORD"
state_set "$RUN_DIR" USER_NAME "Verify User"
state_set "$RUN_DIR" NEXT_DIST "$NEXT_DIST"
state_set "$RUN_DIR" GIT_REV "$(git -C "$VERIFY_REPO" rev-parse --short HEAD)"

# 1) Postgres: private cluster in the run dir, loopback TCP only (no unix socket),
#    password auth. Postgres and Chromium need a host that allows their OS facilities.
echo "[1/4] postgres cluster $PG_DATA :$PG_PORT"
PWFILE="$RUN_DIR/.pwfile"
printf '%s\n' "$PG_PASSWORD" >"$PWFILE"
LC_ALL=en_US.UTF-8 "$PG_BIN/initdb" -D "$PG_DATA" -U moldy -E UTF8 --locale=en_US.UTF-8 \
  --auth-host=scram-sha-256 --auth-local=reject --pwfile="$PWFILE" >"$RUN_DIR/logs/initdb.log" 2>&1
rm -f "$PWFILE"
LC_ALL=en_US.UTF-8 "$PG_BIN/pg_ctl" -D "$PG_DATA" -l "$RUN_DIR/logs/postgres.log" -w -t 60 \
  -o "-p $PG_PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories=''" start \
  >"$RUN_DIR/logs/pg_ctl.log" 2>&1
export PGPASSWORD="$PG_PASSWORD"
wait_until 60 "postgres TCP" "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PG_PORT" -U moldy -d postgres
"$PG_BIN/createdb" -h 127.0.0.1 -p "$PG_PORT" -U moldy moldy
unset PGPASSWORD
DB_ASYNC="postgresql+asyncpg://moldy:$PG_PASSWORD@127.0.0.1:$PG_PORT/moldy"
DB_SYNC="postgresql://moldy:$PG_PASSWORD@127.0.0.1:$PG_PORT/moldy"

# Backend env: no backend/.env (MOLDY_DISABLE_ENV_FILE) so no real keys leak in;
# keyless scripted model + seeded super user; fresh crypto keys per run.
BACKEND_ENV=(
  env -i
  "PATH=$PATH" "HOME=$HOME" "TMPDIR=${TMPDIR:-/tmp}" "LANG=${LANG:-en_US.UTF-8}"
  MOLDY_DISABLE_ENV_FILE=true PYTHON_DOTENV_DISABLED=1
  "DATABASE_URL=$DB_ASYNC" "DATABASE_URL_SYNC=$DB_SYNC"
  "ENCRYPTION_KEYS=$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
  "JWT_SECRET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
  E2E_SCRIPTED_MODEL_ENABLED=true E2E_SEED_USER_ENABLED=true E2E_TEST_HELPERS_ENABLED=true
  "E2E_USER_EMAIL=$USER_EMAIL" "E2E_USER_PASSWORD=$USER_PASSWORD" "E2E_USER_NAME=Verify User"
  RATE_LIMIT_ENABLED=false SKILL_EVALUATION_ENABLED=false
  CHECKPOINTER_POOL_MIN_SIZE=1 CHECKPOINTER_POOL_MAX_SIZE=4
  "SKILL_NODE_MODULES_DIR=$VERIFY_REPO/backend/skill-node/node_modules"
  "CORS_ALLOWED_ORIGINS=http://localhost:$FRONTEND_PORT,http://127.0.0.1:$FRONTEND_PORT"
)

# 2) Migrations against the throwaway DB only
echo "[2/4] alembic upgrade head"
(cd "$VERIFY_REPO/backend" && "${BACKEND_ENV[@]}" ./.venv/bin/alembic upgrade head) \
  >"$RUN_DIR/logs/migrate.log" 2>&1

# 3) Backend. cwd = run data dir so every relative ./data/* path stays inside the run.
echo "[3/4] backend :$BACKEND_PORT"
set -m
(cd "$RUN_DIR/data" && exec "${BACKEND_ENV[@]}" "$VERIFY_REPO/backend/.venv/bin/python" -m uvicorn \
  --app-dir "$VERIFY_REPO/backend" app.main:app --host 127.0.0.1 --port "$BACKEND_PORT") \
  >"$RUN_DIR/logs/backend.log" 2>&1 &
state_set "$RUN_DIR" BACKEND_PGID "$!"
set +m
wait_until 120 "backend /api/health" curl -fsS "http://localhost:$BACKEND_PORT/api/health"

# 4) Next rewrites tsconfig and next-env even with a custom distDir, so run a snapshot.
echo "[4/4] frontend :$FRONTEND_PORT (first compile is slow)"
FRONTEND_DIR="$VERIFY_REPO/frontend/$NEXT_DIST/app"
node "$VERIFY_SKILL_DIR/scripts/prepare-frontend.mjs" "$VERIFY_REPO" "$FRONTEND_DIR" \
  >"$RUN_DIR/logs/frontend-prepare.log" 2>&1
(cd "$FRONTEND_DIR" && pnpm -s prepare:assets) >>"$RUN_DIR/logs/frontend-prepare.log" 2>&1
set -m
(cd "$FRONTEND_DIR" && exec env -i "PATH=$PATH" "HOME=$HOME" "TMPDIR=${TMPDIR:-/tmp}" \
  E2E_NEXT_BUILD_DIR=.next \
  NEXT_PUBLIC_API_BASE_URL="http://localhost:$BACKEND_PORT" NEXT_PUBLIC_CHAT_RUNTIME=langgraph_v3 \
  NEXT_TELEMETRY_DISABLED=1 pnpm exec next dev --port "$FRONTEND_PORT") \
  >"$RUN_DIR/logs/frontend.log" 2>&1 &
state_set "$RUN_DIR" FRONTEND_PGID "$!"
set +m
wait_until 300 "frontend /login" curl -fsS -o /dev/null "http://localhost:$FRONTEND_PORT/login"

"$VERIFY_SKILL_DIR/scripts/doctor.sh" "$RUN_ID"
trap - ERR
echo
echo "RUN_ID=$RUN_ID"
echo "APP_URL=http://localhost:$FRONTEND_PORT  API_URL=http://localhost:$BACKEND_PORT"
echo "evidence: $RUN_DIR/evidence"
