"""Frozen membership grants and legacy resource scope backfill."""

from datetime import datetime
from uuid import UUID, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, JsonValue

from app.migrations.org_authz_v1.contracts import M81_SCOPED, ORG, TENANT, MigrationContext


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
    context: MigrationContext,
    table: sa.Table,
    values: dict[str, JsonValue | UUID | datetime],
) -> None:
    connection, key = context.connection, values["id"]
    if connection.scalar(sa.select(table.c.id).where(table.c.id == key)) is None:
        connection.execute(sa.insert(table).values(**values))


def seed_memberships(context: MigrationContext) -> None:
    connection, metadata = context.connection, context.metadata
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
            context,
            metadata.tables["organization_role_grants"],
            {
                "id": role_id,
                "org_id": ORG,
                "tenant_id": TENANT,
                "role": role,
                "principal_type": "user",
                "principal_id": user.id,
            },
        )
        if user.is_super_user:
            insert_if_missing(
                context,
                metadata.tables["tenant_role_grants"],
                {
                    "id": uuid5(TENANT, f"legacy/{user.id}/admin"),
                    "tenant_id": TENANT,
                    "user_id": user.id,
                    "role": "admin",
                },
            )
        connection.execute(
            sa.update(users)
            .where(users.c.id == user.id, users.c.last_active_org_id.is_(None))
            .values(last_active_org_id=ORG)
        )


def backfill_scopes(context: MigrationContext) -> None:
    connection, metadata = context.connection, context.metadata
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
