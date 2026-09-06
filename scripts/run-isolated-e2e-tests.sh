#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: run-isolated-e2e-tests.sh <scripted|live> --project <project> --manifest <evidence-json> [-- playwright-options]" >&2
  exit 64
}

if [[ $# -lt 5 || "$2" != "--project" || "$4" != "--manifest" ]]; then
  usage
fi

case "$1:$3" in
  scripted:scripted-smoke|scripted:scripted-full|scripted:scripted-capture|live:live-manual) ;;
  *) usage ;;
esac

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
lane="$1"
project="$3"
manifest="$5"
shift 5

exec "$repo_root/backend/.venv/bin/python" "$repo_root/scripts/e2e_test_runner.py" \
  "$lane" --project "$project" --manifest "$manifest" "$@"
