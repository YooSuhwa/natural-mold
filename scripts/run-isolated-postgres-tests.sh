#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: run-isolated-postgres-tests.sh <all|migration-roundtrip|self-test|stream-resume|queue-concurrency> --manifest <evidence-json>" >&2
  echo "   or: run-isolated-postgres-tests.sh run-lifecycle stream-resume --manifest <evidence-json>" >&2
  exit 64
}

if [[ $# -eq 4 && "$1" == "run-lifecycle" && "$2" == "stream-resume" && "$3" == "--manifest" ]]; then
  mode="run-lifecycle+stream-resume"
  manifest="$4"
elif [[ $# -eq 3 && "$2" == "--manifest" ]]; then
  case "$1" in
    all|migration-roundtrip|self-test|stream-resume|queue-concurrency) mode="$1" ;;
    *) usage ;;
  esac
  manifest="$3"
else
  usage
fi

repo_root="$(cd "$(/usr/bin/dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
exec "$repo_root/backend/.venv/bin/python" "$repo_root/scripts/postgres_test_runner.py" \
  "$mode" --manifest "$manifest"
