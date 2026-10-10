"""Derive destination hosts from configured URLs without decrypting secrets."""

from urllib.parse import urlsplit
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy.engine import Connection

from app.credentials import definitions as _definitions  # noqa: F401 - populate metadata
from app.credentials.registry import registry
from schema_drafts.org_authz.schema import schema_metadata

GENERIC = frozenset({"http_api_key", "http_basic", "http_bearer", "mcp_secret"})


class CredentialSource(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    definition_key: str


def hostname(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith(("https://", "http://")):
        return None
    host = urlsplit(value).hostname
    return host.lower().rstrip(".") if host else None


def backfill_modes(connection: Connection) -> None:
    metadata = schema_metadata(later_columns=True)
    credentials = metadata.tables["credentials"]
    for name in ("tools", "mcp_servers"):
        table = metadata.tables[name]
        definition = (
            sa.select(credentials.c.definition_key)
            .where(credentials.c.id == table.c.credential_id)
            .scalar_subquery()
        )
        connection.execute(
            sa.update(table).values(
                credential_mode=sa.case((table.c.credential_id.is_(None), "none"), else_="shared"),
                credential_definition_key=definition,
            )
        )
    destinations: dict[UUID, set[str]] = {}
    servers = metadata.tables["mcp_servers"]
    for credential, url in connection.execute(
        sa.select(servers.c.credential_id, servers.c.url)
    ).all():
        host = hostname(url)
        if credential is not None and host:
            destinations.setdefault(credential, set()).add(host)
    models = metadata.tables["models"]
    for credential, url in connection.execute(
        sa.select(models.c.default_credential_id, models.c.base_url)
    ).all():
        host = hostname(url)
        if credential is not None and host:
            destinations.setdefault(credential, set()).add(host)
    rows = connection.execute(sa.select(credentials.c.id, credentials.c.definition_key)).mappings()
    for row in (CredentialSource.model_validate(raw) for raw in rows):
        hosts = destinations.get(row.id, set())
        definition = registry.get(row.definition_key)
        if definition and definition.test:
            host = hostname(definition.test.request.get("url"))
            if host:
                hosts.add(host)
        if not hosts and row.definition_key not in GENERIC:
            raise ValueError(f"Credential destination requires preflight resolution: {row.id}")
        connection.execute(
            sa.update(credentials)
            .where(credentials.c.id == row.id)
            .values(allowed_hosts=sorted(hosts))
        )
