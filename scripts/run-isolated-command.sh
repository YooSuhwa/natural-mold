#!/usr/bin/env bash

set -u
set -m

usage() {
  echo "usage: run-isolated-command.sh --cwd <backend|frontend> -- <argv...>" >&2
  exit 64
}

if [[ $# -lt 4 || "$1" != "--cwd" || "$3" != "--" ]]; then
  usage
fi

case "$2" in
  backend|frontend) command_cwd="$2" ;;
  *) usage ;;
esac
shift 3
if [[ $# -eq 0 || -z "$1" ]]; then
  usage
fi

repo_root="$(cd "$(/usr/bin/dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
trusted_python="${MOLDY_GATE_PYTHON:-$repo_root/backend/.venv/bin/python}"
if [[ "$trusted_python" != /* || ! -x "$trusted_python" ]]; then
  exit 70
fi
inherited_run_root="${MOLDY_TEST_RUN_ROOT:-}"
run_root="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/.moldy-test-run.XXXXXXXX")" || exit 70
cleanup_helper="$repo_root/scripts/cleanup-isolated-root.py"
prepare_helper="$repo_root/scripts/prepare-isolated-run.py"
manifest_helper="$repo_root/scripts/isolated-manifest.py"
root_identity="$("$trusted_python" "$cleanup_helper" identity "$run_root")" || {
  /bin/rmdir -- "$run_root"
  exit 70
}
root_device="${root_identity%%:*}"
root_inode="${root_identity##*:}"
manifest_path="${MOLDY_CLEANUP_MANIFEST:-}"
manifest_parent_identity=""
manifest_file_identity=""
child_pid=""
interrupted=0
cleaned=0
cleanup_result="cleanup_failed"
manifest_result="not_requested"

if [[ "$("$trusted_python" "$prepare_helper" "$run_root" "$repo_root")" != "prepared" ]]; then
  "$trusted_python" "$cleanup_helper" cleanup "$run_root" "$root_device" "$root_inode" >/dev/null
  exit 70
fi

if [[ -n "$manifest_path" ]]; then
  manifest_identity="$("$trusted_python" "$manifest_helper" prepare "$manifest_path")" || {
    "$trusted_python" "$cleanup_helper" cleanup "$run_root" "$root_device" "$root_inode" >/dev/null
    exit 74
  }
  manifest_parent_identity="${manifest_identity%% *}"
  manifest_file_identity="${manifest_identity##* }"
fi

root_hash() {
  "$trusted_python" -c \
    'import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$run_root"
}

write_manifest() {
  local status="$1"
  local exit_code="$2"
  local digest
  digest="$(root_hash)"
  local payload
  payload="{\"child_exit_code\":${exit_code},\"cleanup\":\"${cleanup_result}\",\"run_root_sha256\":\"${digest}\",\"schema_version\":1,\"status\":\"${status}\"}"
  payload+=$'\n'
  if [[ -n "$manifest_path" ]]; then
    if "$trusted_python" "$manifest_helper" finalize "$manifest_path" "$manifest_parent_identity" "$manifest_file_identity" "$payload" >/dev/null; then
      manifest_result="written"
    else
      manifest_result="failed"
    fi
  else
    printf '%s' "$payload"
    manifest_result="stdout"
  fi
}

terminate_owned_group() {
  if [[ -z "$child_pid" ]] || ! kill -0 -- "-$child_pid" 2>/dev/null; then
    return
  fi
  kill -TERM -- "-$child_pid" 2>/dev/null || true
  for _ in {1..20}; do
    if ! kill -0 -- "-$child_pid" 2>/dev/null; then
      return
    fi
    /bin/sleep 0.05
  done
  kill -KILL -- "-$child_pid" 2>/dev/null || true
}

cleanup() {
  local exit_code="$1"
  local status="$2"
  if [[ "$cleaned" -eq 1 ]]; then
    return
  fi
  cleaned=1
  terminate_owned_group
  if [[ -n "$child_pid" ]]; then
    wait "$child_pid" 2>/dev/null || true
  fi
  cleanup_result="$("$trusted_python" "$cleanup_helper" cleanup "$run_root" "$root_device" "$root_inode")"
  cleanup_exit=$?
  if [[ "$cleanup_exit" -ne 0 && "$cleanup_result" != "identity_mismatch" && "$cleanup_result" != "root_recreated" && "$cleanup_result" != "replacement_detected" ]]; then
    cleanup_result="cleanup_failed"
  fi
  write_manifest "$status" "$exit_code"
}

on_interrupt() {
  interrupted=1
  cleanup 130 interrupted
  exit 130
}

on_terminate() {
  interrupted=1
  cleanup 143 interrupted
  exit 143
}

trap on_interrupt INT
trap on_terminate TERM

export MOLDY_DISABLE_ENV_FILE=true
export PYTHON_DOTENV_DISABLED=1
export MOLDY_TEST_RUN_ROOT="$run_root"
export MOLDY_BACKEND_SOURCE_ROOT="$repo_root/backend"
export MOLDY_FRONTEND_SOURCE_ROOT="$repo_root/frontend"
unset MOLDY_CLEANUP_MANIFEST
if [[ -n "$inherited_run_root" ]]; then
  unset E2E_AUTH_STATE_PATH E2E_NEXT_BUILD_DIR E2E_RESULTS_DIR
fi

if [[ "$command_cwd" == "backend" ]]; then
  child_cwd="$repo_root/backend"
else
  child_cwd="$run_root/frontend"
fi

if [[ -n "$manifest_path" ]]; then
  if ! "$trusted_python" "$manifest_helper" verify "$manifest_path" "$manifest_parent_identity" "$manifest_file_identity" >/dev/null; then
    cleanup 74 failed
    exit 74
  fi
fi

(
  cd "$child_cwd" || exit 70
  exec "$@"
) &
child_pid=$!
wait "$child_pid"
child_exit=$?

if [[ "$interrupted" -eq 1 ]]; then
  exit 130
fi
if [[ "$child_exit" -eq 0 ]]; then
  cleanup "$child_exit" passed
else
  cleanup "$child_exit" failed
fi
child_pid=""
if [[ "$child_exit" -eq 0 && "$cleanup_result" != "removed" ]]; then
  exit 74
fi
if [[ "$child_exit" -eq 0 && "$manifest_result" == "failed" ]]; then
  exit 74
fi
exit "$child_exit"
