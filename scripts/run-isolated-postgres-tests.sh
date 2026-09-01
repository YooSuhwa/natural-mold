#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: run-isolated-postgres-tests.sh <all|self-test|stream-resume> --manifest <evidence-json>" >&2
  exit 64
}

if [[ $# -ne 3 || "$2" != "--manifest" ]]; then
  usage
fi
case "$1" in
  all|self-test|stream-resume) mode="$1" ;;
  *) usage ;;
esac

repo_root="$(cd "$(/usr/bin/dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
exec "$repo_root/backend/.venv/bin/python" "$repo_root/scripts/postgres_test_runner.py" \
  "$mode" --manifest "$3"
