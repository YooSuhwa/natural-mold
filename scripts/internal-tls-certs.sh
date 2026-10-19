#!/usr/bin/env bash
# Generate a private CA and one internal server certificate for the
# docker-compose.tls.yml overlay (org-authz plan 15.2.1, I-09).
#
#   bash scripts/internal-tls-certs.sh [out_dir] [ca_dir]
#     out_dir  ca.crt, server.crt, server.key — mounted into containers (default certs/internal)
#     ca_dir   ca.key — never mounted; keep it offline (default <out_dir parent>/internal-ca)
#
# server.key is 0640 and the directory 0750, owned by your user and primary
# group. The non-root OpenFGA container reads the key through
# `group_add: [MOLDY_TLS_KEY_GID]` (docker-compose.tls.yml); set
# MOLDY_TLS_KEY_GID to the group printed at the end.
#
# For evaluation and the integration lane. Production installs should use
# certificates issued by the company CA (docs/operator-setup.md section 7).
set -euo pipefail

out="${1:-certs/internal}"
ca_dir="${2:-$(dirname "$out")/internal-ca}"
days="${MOLDY_TLS_CERT_DAYS:-825}"
sans="DNS:postgres,DNS:openfga-postgres,DNS:openfga,DNS:localhost,IP:127.0.0.1"
# Extra SANs for hosts outside Compose, e.g. MOLDY_TLS_EXTRA_SANS=DNS:db.corp.example
if [[ -n "${MOLDY_TLS_EXTRA_SANS:-}" ]]; then sans="$sans,$MOLDY_TLS_EXTRA_SANS"; fi

if [[ -e "$out/ca.crt" || -e "$ca_dir/ca.key" ]]; then
  echo "refusing to overwrite existing $out/ca.crt or $ca_dir/ca.key; remove them first" >&2
  exit 1
fi
umask 077
mkdir -p "$out" "$ca_dir"

openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days "$days" \
  -subj "/CN=Moldy internal CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -keyout "$ca_dir/ca.key" -out "$out/ca.crt"

openssl req -newkey rsa:3072 -nodes -sha256 -subj "/CN=moldy-internal" \
  -keyout "$out/server.key" -out "$out/server.csr"

ext="$(mktemp "${TMPDIR:-/tmp}/moldy-tls-ext.XXXXXX")"
trap 'rm -f "$ext"' EXIT
printf '%s\n' "subjectAltName=$sans" "extendedKeyUsage=serverAuth" \
  "keyUsage=critical,digitalSignature,keyEncipherment" > "$ext"
openssl x509 -req -sha256 -days "$days" -in "$out/server.csr" \
  -CA "$out/ca.crt" -CAkey "$ca_dir/ca.key" -CAcreateserial \
  -extfile "$ext" -out "$out/server.crt"
rm -f "$out/server.csr" "$out/ca.srl" "$ca_dir/ca.srl"

# Certificates are public; the key is group-readable only, for the OpenFGA
# container's supplementary group. Postgres copies it to a private 0600 file.
chmod 0750 "$out"
chmod 0644 "$out/ca.crt" "$out/server.crt"
chmod 0640 "$out/server.key"
chmod 0700 "$ca_dir"
chmod 0600 "$ca_dir/ca.key"
echo "wrote $out/{ca.crt,server.crt,server.key} (SAN: $sans)"
echo "CA key: $ca_dir/ca.key — not mounted anywhere; move it off this host"
echo "set MOLDY_TLS_KEY_GID=$(id -g) (group of $out/server.key) in the Compose .env"
