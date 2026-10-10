"""Frozen m81–m83 scope and identity contract."""

from dataclasses import dataclass
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.engine import Connection

M81_SCOPED: Final = (
    "agents",
    "skills",
    "mcp_servers",
    "credentials",
    "tools",
    "marketplace_items",
    "conversations",
    "conversation_artifacts",
    "message_attachments",
    "memory_records",
    "memory_proposals",
    "builder_sessions",
    "skill_builder_sessions",
    "agent_blueprints",
    "marketplace_installations",
    "agent_triggers",
    "agent_deployments",
    "agent_api_keys",
    "credential_defaults",
    "share_links",
)
TENANT_ONLY: Final = ("models", "system_llm_settings", "audit_events")
NAMING: Final = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
}
SHARED: Final = ("agents", "skills", "mcp_servers", "tools", "credentials", "marketplace_items")
TENANT: Final = uuid5(NAMESPACE_URL, "moldy/default-tenant")
ORG: Final = uuid5(NAMESPACE_URL, "moldy/default-organization")


@dataclass(frozen=True, slots=True)
class DefaultIdentity:
    tenant_id: UUID = TENANT
    org_id: UUID = ORG


@dataclass(frozen=True, slots=True)
class MigrationContext:
    connection: Connection
    metadata: sa.MetaData
    identity: DefaultIdentity = DefaultIdentity()
