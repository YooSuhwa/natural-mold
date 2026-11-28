#!/usr/bin/env bash
# Shared helpers for verify-moldy scripts. Source, do not execute.

VERIFY_SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
VERIFY_REPO="$(git -C "$VERIFY_SKILL_DIR" rev-parse --show-toplevel)"
VERIFY_ROOT="$VERIFY_REPO/.verify-moldy"

# resolve_run_dir [run-id] -> prints the run directory (default: .verify-moldy/current)
resolve_run_dir() {
  local id="${1:-${VERIFY_RUN_ID:-}}"
  if [[ -n "$id" ]]; then
    echo "$VERIFY_ROOT/runs/$id"
  elif [[ -L "$VERIFY_ROOT/current" ]]; then
    (cd "$VERIFY_ROOT/current" && pwd -P)
  else
    echo "verify-moldy: no run id given and no .verify-moldy/current link" >&2
    return 1
  fi
}

# load_state <run-dir> -> sources state.env into the current shell
load_state() {
  local state="$1/state.env"
  if [[ ! -f "$state" ]]; then
    echo "verify-moldy: missing $state" >&2
    return 1
  fi
  # shellcheck disable=SC1090
  source "$state"
}

# state_set <run-dir> KEY VALUE -> appends/overrides a key in state.env
state_set() {
  local state="$1/state.env"
  printf '%s=%q\n' "$2" "$3" >>"$state"
}

free_port() {
  python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'
}

# wait_until <timeout-seconds> <description> <command...>
wait_until() {
  local timeout="$1" what="$2"
  shift 2
  local start=$SECONDS
  until "$@" >/dev/null 2>&1; do
    if ((SECONDS - start >= timeout)); then
      echo "verify-moldy: timed out after ${timeout}s waiting for $what" >&2
      return 1
    fi
    sleep 2
  done
}
