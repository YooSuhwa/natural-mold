"""F-11 (plan m87 ``credentials.allowed_hosts``, Q14, D18): fill definition hosts.

F-04 filled ``allowed_hosts`` only from MCP server URLs; credentials used by registry
tools stayed NULL because definitions declared no hosts. This adds each type's hosts
(``CredentialDefinition.hosts``, frozen here as a snapshot) to its existing rows, kept
as a sorted union with hosts already present. Generic HTTP credentials
(``http_bearer``/``http_api_key``/``http_basic``) get the host of the stored ``url`` of
each HTTP tool they are attached to, like F-04 did for MCP servers (IP literals and
templated URLs skipped). Credentials with neither stay as they are (Q14). Data only;
downgrade leaves the rows as they are (the added hosts cannot be told apart from ones
an owner set).
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

import sqlalchemy as sa

from alembic import op

revision = "f11_credential_definition_hosts"
down_revision = "f01_connection_delegation"
branch_labels = None
depends_on = None

DEFINITION_HOSTS: dict[str, tuple[str, ...]] = {
    "anthropic": ("api.anthropic.com",),
    "azure_openai": ("*.openai.azure.com",),
    "coupang_partners": ("api-gateway.coupang.com",),
    "dart_api": ("opendart.fss.or.kr",),
    "google_genai": ("generativelanguage.googleapis.com",),
    "google_search": ("www.googleapis.com",),
    "google_workspace_oauth2": (
        "oauth2.googleapis.com",
        "gmail.googleapis.com",
        "www.googleapis.com",
    ),
    "kipris_plus_api": ("plus.kipris.or.kr",),
    "naver_search": ("openapi.naver.com",),
    "odsay_api": ("api.odsay.com",),
    "openai": ("api.openai.com",),
    "openrouter": ("openrouter.ai",),
}


GENERIC_HTTP = ("http_api_key", "http_basic", "http_bearer")


def _url_host(parameters: object) -> str | None:
    url = parameters.get("url") if isinstance(parameters, dict) else None
    if not isinstance(url, str) or "{" in url:
        return None
    host = (urlsplit(url.strip()).hostname or "").rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host or None
    return None


def _tool_hosts(bind: sa.Connection) -> dict[object, set[str]]:
    tools = sa.table(
        "tools",
        sa.column("credential_id"),
        sa.column("definition_key", sa.String()),
        sa.column("parameters", sa.JSON()),
    )
    query = sa.select(tools.c.credential_id, tools.c.parameters).where(
        tools.c.credential_id.is_not(None), tools.c.definition_key == "http_request"
    )
    found: dict[object, set[str]] = {}
    for credential_id, parameters in bind.execute(query):
        host = _url_host(parameters)
        if host:
            found.setdefault(credential_id, set()).add(host)
    return found


def upgrade() -> None:
    bind = op.get_bind()
    credentials = sa.table(
        "credentials",
        sa.column("id"),
        sa.column("definition_key", sa.String()),
        sa.column("allowed_hosts", sa.JSON()),
    )
    tool_hosts = _tool_hosts(bind)
    rows = bind.execute(
        sa.select(
            credentials.c.id, credentials.c.definition_key, credentials.c.allowed_hosts
        ).where(credentials.c.definition_key.in_(sorted({*DEFINITION_HOSTS, *GENERIC_HTTP})))
    )
    for credential_id, key, current in rows.all():
        extra = tool_hosts.get(credential_id, set()) if key in GENERIC_HTTP else set()
        hosts = sorted({*(current or []), *DEFINITION_HOSTS.get(key, ()), *extra})
        if hosts != list(current or []):
            bind.execute(
                credentials.update()
                .where(credentials.c.id == credential_id)
                .values(allowed_hosts=hosts)
            )


def downgrade() -> None:
    """Data only: nothing to undo."""
