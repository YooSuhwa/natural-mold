#!/usr/bin/env bash
# I-09 TLS integration lane (org-authz plan 15.2.1; done-when "TLS 구성에서 통합 레인 통과").
#
#   bash scripts/run-internal-tls-lane.sh
#
# Brings up postgres + openfga-postgres + openfga with docker-compose.tls.yml in a
# throwaway Compose project (fresh certs, loopback-only ports), runs
# tests/authz/test_i09_tls_lane.py from the host, then removes the
# project and its volumes. Requires Docker and openssl.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
run_dir="$(mktemp -d "${TMPDIR:-/tmp}/moldy-tls-lane.XXXXXX")"
project="moldy-tls-lane-$$"
pg_port="${MOLDY_TLS_LANE_PG_PORT:-15432}"
fga_port="${MOLDY_TLS_LANE_FGA_PORT:-18443}"
python_bin="${MOLDY_GATE_PYTHON:-$root/backend/.venv/bin/python}"

# ca.key goes to a sibling directory that is never mounted into containers.
bash "$root/scripts/internal-tls-certs.sh" "$run_dir/certs" "$run_dir/ca" >/dev/null

export MOLDY_TLS_CERT_DIR="$run_dir/certs" MOLDY_TLS_KEY_GID="$(id -g)"
export MOLDY_TLS_LANE_PG_PORT="$pg_port"
export MOLDY_TLS_LANE_FGA_PORT="$fga_port"
OPENFGA_DB_PASSWORD="$(openssl rand -hex 16)"
OPENFGA_API_TOKEN="$(openssl rand -hex 16)"
export OPENFGA_DB_PASSWORD OPENFGA_API_TOKEN

compose=(docker compose -p "$project" -f "$root/docker-compose.yml"
  -f "$root/docker-compose.tls.yml" -f "$root/docker-compose.tls-lane.yml")
cleanup() {
  "${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$run_dir"
}
trap cleanup EXIT

"${compose[@]}" up -d --wait postgres openfga

ca="$run_dir/certs/ca.crt"
cd "$root/backend"
PGSSLROOTCERT="$ca" \
I09_TLS_CA_CERT="$ca" \
I09_TLS_DATABASE_URL="postgresql+asyncpg://moldy:moldy@localhost:$pg_port/moldy?ssl=verify-full" \
I09_TLS_DATABASE_URL_SYNC="postgresql://moldy:moldy@localhost:$pg_port/moldy?sslmode=verify-full&sslrootcert=$ca" \
I09_TLS_OPENFGA_URL="https://localhost:$fga_port" \
I09_TLS_OPENFGA_TOKEN="$OPENFGA_API_TOKEN" \
  "$python_bin" -m pytest -q -p no:cacheprovider -rs tests/authz/test_i09_tls_lane.py
