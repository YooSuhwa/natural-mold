#!/usr/bin/env bash
# Run with the project's backend Python, Node 22, pnpm, Chromium, and PostgreSQL 16 installed.
# MOLDY_GATE_PYTHON and PG_BINDIR can select the prepared tools in a secondary worktree.
set -euo pipefail
if [[ ${1:-} != --isolated ]]; then
  exec env -i PATH="$PATH" HOME="$HOME" USER="${USER:-moldy}" TMPDIR="${TMPDIR:-/tmp}" \
    MOLDY_GATE_PYTHON="${MOLDY_GATE_PYTHON:-}" PG_BINDIR="${PG_BINDIR:-}" \
    E2E_RESEARCH_BASE_PORT="${E2E_RESEARCH_BASE_PORT:-9336}" \
    PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-}" bash "$0" --isolated
fi

export LC_ALL=C
umask 077
repo=$(cd "$(dirname "$0")/.." && pwd -P)
py=${MOLDY_GATE_PYTHON:-"$repo/backend/.venv/bin/python"}
pgbin=${PG_BINDIR:-$(pg_config --bindir)}
base=$E2E_RESEARCH_BASE_PORT
[[ $base =~ ^[0-9]+$ && $base -ge 1024 && $base -le 65532 ]]
[[ $(node -p 'process.versions.node.split(".")[0]') == 22 ]]
[[ $("$pgbin/postgres" --version) == *" 16."* ]]
[[ -z $PLAYWRIGHT_BROWSERS_PATH ]] && unset PLAYWRIGHT_BROWSERS_PATH

run=$(mktemp -d "/tmp/moldy-research-e2e-XXXXXXXX")
backend_pid='' frontend_pid=''
cleanup() {
  local status=$?
  if [[ $status != 0 ]]; then
    for log in initdb postgres migrations seed backend frontend playwright; do
      [[ -f $run/$log.log ]] && tail -12 "$run/$log.log" >&2
    done
  fi
  for pid in "$frontend_pid" "$backend_pid"; do
    if [[ -n $pid ]]; then kill "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; fi
  done
  if [[ -f $run/pg/postmaster.pid ]]; then "$pgbin/pg_ctl" -D "$run/pg" -m fast -w stop >/dev/null; fi
  rm -rf "$run"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
mkdir -p "$run/socket" "$run/data" "$run/workspace"
"$py" - "$base" <<'PY'
import socket, sys
for port in range(int(sys.argv[1]), int(sys.argv[1]) + 4):
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(f"Loopback port {port} already has a listener")
PY

export PGUSER=moldy PGPORT="$base" PGDATABASE="moldy_e2e_scripted_research_$base"
export PGPASSWORD; PGPASSWORD=$("$py" -c 'import secrets; print(secrets.token_hex(18))')
"$pgbin/initdb" -D "$run/pg" -U "$PGUSER" --auth-local=trust --auth-host=scram-sha-256 \
  --pwfile=<(printf '%s\n' "$PGPASSWORD") --no-locale -E UTF8 > "$run/initdb.log"
"$pgbin/pg_ctl" -D "$run/pg" -l "$run/postgres.log" -o "-h 127.0.0.1 -p $base -k '$run/socket'" -w start
"$pgbin/createdb" -h "$run/socket" "$PGDATABASE"
dsn="$PGUSER:$PGPASSWORD@127.0.0.1:$base/$PGDATABASE"
export DATABASE_URL="postgresql+asyncpg://$dsn" DATABASE_URL_SYNC="postgresql://$dsn"
export DATA_ROOT="$run/data" MOLDY_DISABLE_ENV_FILE=true PYTHON_DOTENV_DISABLED=1
export E2E_SEED_USER_ENABLED=true E2E_SCRIPTED_MODEL_ENABLED=true E2E_TEST_HELPERS_ENABLED=true
export E2E_RESEARCH_FIXTURE=1 MOLDY_RESEARCH_E2E=1 E2E_LANE=scripted RATE_LIMIT_ENABLED=false
export E2E_BACKEND_PORT=$((base+1)) E2E_FRONTEND_PORT=$((base+2)) E2E_RESEARCH_FIXTURE_PORT=$((base+3))
export E2E_BASE_URL="http://localhost:$E2E_FRONTEND_PORT" E2E_API_BASE_URL="http://localhost:$E2E_BACKEND_PORT"
export CORS_ALLOWED_ORIGINS="$E2E_BASE_URL,http://127.0.0.1:$E2E_FRONTEND_PORT"
export NEXT_PUBLIC_API_BASE_URL="$E2E_API_BASE_URL" NEXT_TELEMETRY_DISABLED=1
export NO_PROXY=localhost,127.0.0.1,::1 E2E_USER_EMAIL=playwright-e2e@moldy.dev E2E_USER_NAME="E2E Research"
export E2E_USER_PASSWORD; E2E_USER_PASSWORD=$("$py" -c 'import secrets; print(secrets.token_hex(18))')
export E2E_AUTH_STATE_PATH="$run/auth.json" TAVILY_API_KEY=loopback-fixture
export JWT_SECRET ENCRYPTION_KEYS; JWT_SECRET=$("$py" -c 'import secrets; print(secrets.token_hex(48))')
ENCRYPTION_KEYS=$("$py" -c 'import secrets; print(secrets.token_hex(32))')

cd "$repo/backend"
MOLDY_TEST_RUN_ROOT="$run" "$py" -m alembic upgrade head > "$run/migrations.log" 2>&1
MOLDY_TEST_RUN_ROOT="$run" "$py" -m tests.e2e_research_seed > "$run/seed.log" 2>&1
MOLDY_TEST_RUN_ROOT="$run" "$py" -m uvicorn --factory tests.fixtures.research_search_lane:create_app --host 127.0.0.1 \
  --port "$E2E_BACKEND_PORT" > "$run/backend.log" 2>&1 &
backend_pid=$!

# Turbopack resolves packages within the workspace root. Keep its source and complete
# pnpm link graph inside the throwaway workspace, including when node_modules is shared.
"$py" - "$repo" "$run/workspace" <<'PY'
import errno, os, shutil, sys
from pathlib import Path
repo, mirror = map(Path, sys.argv[1:])
def copy_file(src, dst):
    try:
        os.link(src, dst)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.copy2(src, dst)
deps = (repo / "frontend/node_modules").resolve(strict=True)
store = next(p.parent for p in (deps / "next").resolve().parents if p.name == ".pnpm")
shutil.copytree(repo / "frontend", mirror / "frontend", ignore=shutil.ignore_patterns(
    ".next*", "node_modules", ".env*", ".auth", "test-results", "playwright-report", "coverage", "*.tsbuildinfo"
))
for src, dst in [(store, mirror / "node_modules"), (deps, mirror / "frontend/node_modules")]:
    shutil.copytree(src, dst, symlinks=True, copy_function=copy_file)
for name in ("package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml"):
    shutil.copy2(repo / name, mirror / name)
skill_deps = deps.parents[1] / "backend/skill-node/node_modules"
target = mirror / "backend/skill-node/node_modules"
target.parent.mkdir(parents=True)
target.symlink_to(skill_deps.resolve(strict=True), target_is_directory=True)
PY
cd "$run/workspace/frontend"
# pnpm's installed CLI shim contains absolute paths to the original workspace.
# Supply the equivalent module lookup paths inside this copy for Turbopack SSR.
export NODE_PATH
NODE_PATH=$(node -p 'require("node:path").dirname(require("node:fs").realpathSync("node_modules/next"))'):"$run/workspace/node_modules/.pnpm/node_modules"
pnpm prepare:assets > "$run/frontend.log" 2>&1
node node_modules/next/dist/bin/next dev --turbopack --hostname 127.0.0.1 \
  --port "$E2E_FRONTEND_PORT" >> "$run/frontend.log" 2>&1 &
frontend_pid=$!
for url in "$E2E_API_BASE_URL/api/health" "http://127.0.0.1:$E2E_RESEARCH_FIXTURE_PORT/observations" "$E2E_BASE_URL/login"; do
  curl --silent --fail --retry 30 --retry-connrefused --retry-delay 1 --max-time 120 --output /dev/null "$url"
done
cat > research.e2e.config.cjs <<'JS'
module.exports = {
  testDir: './e2e', testMatch: '**/deep-research-upgrade-fallback.spec.ts',
  globalSetup: './e2e/global-setup.mjs', workers: 1, retries: 0, timeout: 180000,
  reporter: [['list'], ['json', { outputFile: process.env.RESEARCH_E2E_REPORT }]],
  projects: [{ name: 'scripted-full' }],
  use: { baseURL: process.env.E2E_BASE_URL, storageState: process.env.E2E_AUTH_STATE_PATH, timezoneId: 'UTC' }
}
JS
export RESEARCH_E2E_REPORT="$run/report.json"
node node_modules/@playwright/test/cli.js test --config research.e2e.config.cjs --project=scripted-full \
  e2e/deep-research-upgrade-fallback.spec.ts > "$run/playwright.log" 2>&1
cat "$run/playwright.log"
"$py" - "$run/report.json" "$repo" <<'PY'
import base64, json, sys
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
report = json.loads(Path(sys.argv[1]).read_text())
test = report["suites"][0]["specs"][0]["tests"][0]
result, = test["results"]
if result["status"] != "passed":
    raise SystemExit("Browser acceptance did not pass")
receipt, = [a for a in result["attachments"] if a["name"] == "research-search-acceptance"]
evidence = json.loads(base64.b64decode(receipt["body"]))
stamp = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
out = Path(sys.argv[2], "output", "e2e-captures", f"{stamp}-research-search", "acceptance.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
print(out)
PY
