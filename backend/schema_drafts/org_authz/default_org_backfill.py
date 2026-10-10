"""Idempotent default tenant/org backfill with explicit unresolved-owner failure."""

from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter
from sqlalchemy.engine import Connection

from app.authz.config import AuthzSettings
from schema_drafts.org_authz.conversation_backfill import resolve_conversations
from schema_drafts.org_authz.schema import schema_metadata
from schema_drafts.org_authz.scope_columns import M81_SCOPED

TENANT = uuid5(NAMESPACE_URL, "moldy/default-tenant")
ORG = uuid5(NAMESPACE_URL, "moldy/default-organization")


class UserSource(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    is_super_user: bool


class OwnerSource(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    owner: UUID | None
    is_system: bool = False
    agent_id: UUID | None = None


def insert_if_missing(
    connection: Connection,
    table: sa.Table,
    key: UUID,
    values: dict[str, JsonValue | UUID | datetime],
) -> None:
    if connection.scalar(sa.select(table.c.id).where(table.c.id == key)) is None:
        connection.execute(sa.insert(table).values(id=key, **values))


def seed_memberships(connection: Connection, metadata: sa.MetaData) -> None:
    users = metadata.tables["users"]
    rows = connection.execute(
        sa.select(users.c.id, users.c.is_super_user).where(users.c.is_active.is_(True))
    ).mappings()
    for user in (UserSource.model_validate(row) for row in rows):
        for name, scope, scope_id in (
            ("tenant_members", "tenant_id", TENANT),
            ("organization_members", "org_id", ORG),
        ):
            table = metadata.tables[name]
            exists = connection.scalar(
                sa.select(table.c.user_id).where(
                    table.c[scope] == scope_id, table.c.user_id == user.id
                )
            )
            if exists is None:
                values: dict[str, UUID | str] = {
                    scope: scope_id,
                    "user_id": user.id,
                    "status": "active",
                }
                if name == "organization_members":
                    values["tenant_id"] = TENANT
                connection.execute(sa.insert(table).values(**values))
        role = "admin" if user.is_super_user else "builder"
        role_id = uuid5(ORG, f"legacy/{user.id}/{role}")
        insert_if_missing(
            connection,
            metadata.tables["organization_role_grants"],
            role_id,
            {
                "org_id": ORG,
                "tenant_id": TENANT,
                "role": role,
                "principal_type": "user",
                "principal_id": user.id,
            },
        )
        if user.is_super_user:
            insert_if_missing(
                connection,
                metadata.tables["tenant_role_grants"],
                uuid5(TENANT, f"legacy/{user.id}/admin"),
                {"tenant_id": TENANT, "user_id": user.id, "role": "admin"},
            )
        connection.execute(
            sa.update(users)
            .where(users.c.id == user.id, users.c.last_active_org_id.is_(None))
            .values(last_active_org_id=ORG)
        )


def backfill_scopes(connection: Connection, metadata: sa.MetaData) -> None:
    agents = metadata.tables["agents"]
    agent_owners = dict(connection.execute(sa.select(agents.c.id, agents.c.user_id)).tuples().all())
    for name in M81_SCOPED:
        table = metadata.tables[name]
        owner = (
            "user_id"
            if "user_id" in table.c
            else "owner_user_id"
            if "owner_user_id" in table.c
            else "created_by"
        )
        columns = [table.c.id, table.c[owner].label("owner")]
        for optional in ("is_system", "agent_id"):
            if optional in table.c:
                columns.append(table.c[optional])
        rows = connection.execute(sa.select(*columns).where(table.c.org_id.is_(None))).mappings()
        for row in (OwnerSource.model_validate(raw) for raw in rows):
            if row.is_system:
                continue
            user = row.owner or (agent_owners.get(row.agent_id) if row.agent_id else None)
            if user is None:
                raise ValueError(f"Cannot resolve non-system owner: {name}/{row.id}")
            connection.execute(
                sa.update(table).where(table.c.id == row.id).values(org_id=ORG, tenant_id=TENANT)
            )
    credentials = metadata.tables["credentials"]
    connection.execute(
        sa.update(credentials).where(credentials.c.is_system.is_(True)).values(scope="platform")
    )


def backfill(connection: Connection, settings: AuthzSettings | None = None) -> None:
    config = settings or AuthzSettings()
    metadata = schema_metadata(scope_columns=True)
    limits = TypeAdapter(dict[str, JsonValue]).validate_json(config.tenant_default_limits)
    insert_if_missing(
        connection,
        metadata.tables["tenants"],
        TENANT,
        {
            "name": config.default_tenant_name,
            "slug": "moldy-default",
            "limits": limits,
            "settings": {},
            "secrets_namespace": f"tenants/{TENANT}",
            "data_keys": {},
        },
    )
    insert_if_missing(
        connection,
        metadata.tables["organizations"],
        ORG,
        {
            "tenant_id": TENANT,
            "name": config.default_org_name,
            "slug": "default",
            "is_default": True,
            "settings": {"default_member_role": "member"},
        },
    )
    seed_memberships(connection, metadata)
    resolve_conversations(connection, metadata)
    backfill_scopes(connection, metadata)


def reverse_backfill(connection: Connection) -> None:
    """Undo default scope without deleting content; extra dependencies block rollback.

    A post-migration group, invitation or another organization in the default
    tenant prevents FK deletion and rolls back this entire operation. Do not
    silently remove live organization data to make a downgrade succeed.
    """
    metadata = schema_metadata(scope_columns=True)
    conversations = metadata.tables["conversations"]
    original_conversations = connection.scalars(
        sa.select(conversations.c.id).where(
            conversations.c.org_id == ORG, conversations.c.tenant_id == TENANT
        )
    ).all()
    for name in M81_SCOPED:
        table = metadata.tables[name]
        connection.execute(
            sa.update(table)
            .where(table.c.org_id == ORG, table.c.tenant_id == TENANT)
            .values(org_id=None, tenant_id=None)
        )
    users = metadata.tables["users"]
    connection.execute(
        sa.update(users).where(users.c.last_active_org_id == ORG).values(last_active_org_id=None)
    )
    connection.execute(
        sa.update(conversations)
        .where(conversations.c.id.in_(original_conversations))
        .values(user_id=None)
    )
    for name in ("organization_role_grants", "organization_members"):
        table = metadata.tables[name]
        connection.execute(sa.delete(table).where(table.c.org_id == ORG))
    for name in ("tenant_role_grants", "tenant_members"):
        table = metadata.tables[name]
        connection.execute(sa.delete(table).where(table.c.tenant_id == TENANT))
    connection.execute(
        sa.delete(metadata.tables["organizations"]).where(
            metadata.tables["organizations"].c.id == ORG
        )
    )
    connection.execute(
        sa.delete(metadata.tables["tenants"]).where(metadata.tables["tenants"].c.id == TENANT)
    )
