#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
exec "$repo_root/backend/.venv/bin/python" "$repo_root/scripts/project_gate_runner.py" "$@"
