"""Internal-hop TLS checks for the production boot validator (plan 15.2.1, I-09).

``APP_ENV=production`` refuses to start when an internal connection would
travel in plaintext or with certificate verification turned off:

* PostgreSQL URLs (``DATABASE_URL``, ``MIGRATION_DATABASE_URL`` for asyncpg,
  ``DATABASE_URL_SYNC`` for psycopg/libpq) must ask for ``verify-full``.
* ``OPENFGA_API_URL`` and ``VAULT_URL`` (when set) must be ``https``.
* ``OPENFGA_CA_CERT`` / ``VAULT_CA_CERT``, when set, must point at a file.

The only exception is ``INTERNAL_PLAINTEXT_HOSTS`` (comma-separated host
names, default empty): a same-host Compose install lists its service names
there. Messages name the setting and ``host:port`` only, never credentials.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from app.config import Settings

VERIFY_FULL = "verify-full"
_EXEMPT_HINT = (
    "or list the host in INTERNAL_PLAINTEXT_HOSTS (same-host Compose installs only; "
    "see docs/operator-setup.md section 7)"
)
# (setting name, attribute, driver); asyncpg reads ``ssl``, libpq reads ``sslmode``.
_DATABASE_URLS: tuple[tuple[str, str, str], ...] = (
    ("DATABASE_URL", "database_url", "asyncpg"),
    ("MIGRATION_DATABASE_URL", "migration_database_url", "asyncpg"),
    ("DATABASE_URL_SYNC", "database_url_sync", "libpq"),
)
_SERVICE_URLS: tuple[tuple[str, str, str], ...] = (
    ("OPENFGA_API_URL", "openfga_api_url", "OPENFGA_CA_CERT"),
    ("VAULT_URL", "vault_url", "VAULT_CA_CERT"),
)
_CA_FILES: tuple[tuple[str, str], ...] = (
    ("OPENFGA_CA_CERT", "openfga_ca_cert"),
    ("VAULT_CA_CERT", "vault_ca_cert"),
)


def plaintext_exempt_hosts(settings: Settings) -> frozenset[str]:
    """Hosts allowed to stay plaintext; empty by default (15.2 ``INTERNAL_PLAINTEXT_HOSTS``)."""
    raw = settings.internal_plaintext_hosts or ""
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def _endpoint(host: str | None, port: int | None) -> str:
    return f"{host or '<no host>'}:{port}" if port else (host or "<no host>")


def _query_values(url: URL, key: str) -> tuple[str, ...]:
    value = url.query.get(key)
    if value is None:
        return ()
    return value if isinstance(value, tuple) else (value,)


def _query_value(url: URL, key: str) -> str | None:
    values = _query_values(url, key)
    return values[-1].lower() if values and values[-1] else None


def _is_unix_socket(url: URL) -> bool:
    """libpq/asyncpg ``?host=/run/postgresql`` with no network host: never leaves the machine."""
    hosts = _query_values(url, "host")
    return (
        not url.host
        and not _query_values(url, "hostaddr")
        and bool(hosts)
        and all(h.startswith("/") for h in hosts)
    )


def _database_issue(name: str, raw: str, driver: str, exempt: frozenset[str]) -> str | None:
    try:
        url = make_url(raw)
    except ArgumentError:
        return f"{name} is not a valid database URL."
    if url.drivername.startswith("sqlite") or _is_unix_socket(url):
        return None  # a local file or socket is not an internal network hop
    # A ``host``/``hostaddr`` query override decides where libpq/asyncpg really
    # connect, so the URL's host name cannot vouch for a plaintext exemption.
    overrides = _query_values(url, "host") + _query_values(url, "hostaddr")
    if not overrides and (url.host or "").lower() in exempt:
        return None
    mode = _query_value(url, "ssl") or _query_value(url, "sslmode")
    where = _endpoint(url.host or ",".join(overrides) or None, url.port)
    fix = (
        "Append `?ssl=verify-full` and set PGSSLROOTCERT to the internal CA file"
        if driver == "asyncpg"
        else "Append `?sslmode=verify-full&sslrootcert=/certs/ca.crt`"
    )
    if mode is None:
        hint = (
            "(INTERNAL_PLAINTEXT_HOSTS does not apply to URLs with host/hostaddr query overrides)"
            if overrides
            else _EXEMPT_HINT
        )
        return f"{name} connects to {where} without TLS. {fix}, {hint}."
    if mode != VERIFY_FULL:
        return (
            f"{name} connects to {where} with TLS mode '{mode}', which does not verify "
            f"the server certificate and host name. Use {VERIFY_FULL} ({fix})."
        )
    return None


def _service_issue(name: str, raw: str, ca_name: str, exempt: frozenset[str]) -> str | None:
    if not raw:
        return None
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" or host in exempt:
        return None
    try:
        port = parts.port
    except ValueError:
        port = None
    return (
        f"{name} uses '{parts.scheme or 'no scheme'}' to {_endpoint(parts.hostname, port)}. "
        f"Serve it over https and use an https:// URL (private CA: set {ca_name}), "
        f"{_EXEMPT_HINT}."
    )


def collect_internal_tls_issues(settings: Settings) -> list[str]:
    """Return one actionable message per internal hop that is not verified TLS."""
    exempt = plaintext_exempt_hosts(settings)
    issues: list[str] = []
    for name, attr, driver in _DATABASE_URLS:
        raw = getattr(settings, attr) or ""
        issue = _database_issue(name, raw, driver, exempt) if raw else None
        if issue:
            issues.append(issue)
    for name, attr, ca_name in _SERVICE_URLS:
        issue = _service_issue(name, getattr(settings, attr) or "", ca_name, exempt)
        if issue:
            issues.append(issue)
    for name, attr in _CA_FILES:
        path = getattr(settings, attr) or ""
        if path and not os.path.isfile(path):  # noqa: PTH113 — plain path check, no $HOME use
            issues.append(f"{name} points to a missing file ({path}). Mount the CA bundle there.")
    return issues
