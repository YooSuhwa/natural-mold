"""A clean m82 round trip restores only recorded original values."""

from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from schema_drafts.org_authz import m82_default_org_backfill as m82
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.test_default_backfill_rollback import prepared_connection as prepared_connection
from tests.authz.test_schema_draft_migrations import execute


async def test_clean_rollback_restores_existing_owner_scope_and_user_preferences(
    prepared_connection: AsyncConnection,
) -> None:
    # Given: the original state includes an explicit owner and an existing tenant scope.
    connection = prepared_connection
    metadata = schema_metadata(scope_columns=True)
    conversations = metadata.tables["conversations"]
    users = metadata.tables["users"]
    agents = metadata.tables["agents"]
    prior_tenant, prior_org = uuid4(), uuid4()
    await connection.execute(
        sa.insert(metadata.tables["tenants"]).values(
            id=prior_tenant,
            name="Prior tenant",
            slug="prior",
            secrets_namespace="prior",
        )
    )
    await connection.execute(
        sa.insert(metadata.tables["organizations"]).values(
            id=prior_org,
            tenant_id=prior_tenant,
            name="Prior org",
            slug="prior",
        )
    )
    conversation_id = await connection.scalar(sa.select(conversations.c.id))
    owner = await connection.scalar(sa.select(users.c.id))
    await connection.execute(
        sa.update(conversations)
        .where(
            conversations.c.id == conversation_id,
        )
        .values(user_id=owner)
    )
    await connection.execute(
        sa.update(users)
        .where(users.c.id == owner)
        .values(
            last_active_org_id=prior_org,
        )
    )
    await connection.execute(sa.update(agents).values(tenant_id=prior_tenant))
    before_owners = dict(
        (
            await connection.execute(
                sa.select(
                    conversations.c.id,
                    conversations.c.user_id,
                )
            )
        )
        .tuples()
        .all()
    )
    before_users = dict(
        (
            await connection.execute(
                sa.select(
                    users.c.id,
                    users.c.last_active_org_id,
                )
            )
        )
        .tuples()
        .all()
    )
    before_timestamps = dict(
        (
            await connection.execute(
                sa.select(
                    conversations.c.id,
                    conversations.c.updated_at,
                )
            )
        )
        .tuples()
        .all()
    )
    # When: a clean upgrade and downgrade run without later data edits.
    await connection.run_sync(execute, m82.upgrade)
    await connection.run_sync(execute, m82.downgrade)
    # Then: the exact original owner, scope, preference and timestamp values are restored.
    assert (
        dict(
            (
                await connection.execute(
                    sa.select(
                        conversations.c.id,
                        conversations.c.user_id,
                    )
                )
            )
            .tuples()
            .all()
        )
        == before_owners
    )
    assert (
        dict(
            (
                await connection.execute(
                    sa.select(
                        users.c.id,
                        users.c.last_active_org_id,
                    )
                )
            )
            .tuples()
            .all()
        )
        == before_users
    )
    assert (
        dict(
            (
                await connection.execute(
                    sa.select(
                        conversations.c.id,
                        conversations.c.updated_at,
                    )
                )
            )
            .tuples()
            .all()
        )
        == before_timestamps
    )
    assert (await connection.scalars(sa.select(agents.c.tenant_id))).all() == [prior_tenant] * 2
    assert await connection.scalar(sa.select(metadata.tables["credentials"].c.scope)) == "personal"
    assert await connection.scalar(sa.select(metadata.tables["tenants"].c.id)) == prior_tenant
    assert await connection.scalar(sa.select(metadata.tables["organizations"].c.id)) == prior_org
