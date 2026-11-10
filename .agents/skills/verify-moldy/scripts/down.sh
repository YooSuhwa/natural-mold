#!/usr/bin/env bash
# Stop this run's process groups and Postgres cluster, keeping logs and evidence.
# Usage: down.sh [run-id]   (default: .verify-moldy/current). Idempotent.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

RUN_DIR="$(resolve_run_dir "${1:-}")" || exit 2
load_state "$RUN_DIR" || exit 2

stop_group() {
  local pgid="${1:-}" name="$2"
  [[ -z "$pgid" ]] && return 0
  if kill -0 -- "-$pgid" 2>/dev/null; then
    kill -TERM -- "-$pgid" 2>/dev/null
    for _ in $(seq 1 15); do kill -0 -- "-$pgid" 2>/dev/null || break; sleep 1; done
    kill -KILL -- "-$pgid" 2>/dev/null
    for _ in $(seq 1 5); do kill -0 -- "-$pgid" 2>/dev/null || break; sleep 1; done
    if kill -0 -- "-$pgid" 2>/dev/null; then
      echo "verify-moldy: $name group $pgid is still alive; scratch data retained" >&2
      return 1
    fi
    echo "stopped $name (pgid $pgid)"
  fi
}

stop_group "${FRONTEND_PGID:-}" frontend || exit 1
stop_group "${BACKEND_PGID:-}" backend || exit 1
if [[ -n "${PG_DATA:-}" && -f "$PG_DATA/postmaster.pid" ]]; then
  if ! "$PG_BIN/pg_ctl" -D "$PG_DATA" -m fast -w stop; then
    echo "verify-moldy: postgres stop failed; scratch data retained" >&2
    exit 1
  fi
  echo "stopped postgres $PG_DATA"
fi
rm -rf "$RUN_DIR/data" "${PG_DATA:-$RUN_DIR/pg}" || exit 1
if [[ -n "${NEXT_DIST:-}" ]]; then
  rm -rf "$VERIFY_REPO/frontend/$NEXT_DIST" || exit 1
fi
rmdir "$VERIFY_REPO/frontend/.next-verify" 2>/dev/null || true
date -u +%FT%TZ >"$RUN_DIR/CLEANED"
echo "cleaned run $RUN_ID; evidence kept at $RUN_DIR/evidence, logs at $RUN_DIR/logs"
