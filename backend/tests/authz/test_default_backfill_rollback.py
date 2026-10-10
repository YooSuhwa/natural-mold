"""m82 rollback must verify provenance before changing live default-org data."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Literal, assert_never
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.database import Base
from schema_drafts.org_authz import m78_tenants_organizations as m78
from schema_drafts.org_authz import m79_groups as m79
from schema_drafts.org_authz import m80_org_capabilities_invitations as m80
from schema_drafts.org_authz import m81_org_scope_columns as m81
from schema_drafts.org_authz import m82_default_org_backfill as m82
from schema_drafts.org_authz.default_org_backfill import ORG, TENANT
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.backfill_fixture import seed
from tests.authz.test_schema_draft_migrations import execute


@pytest.fixture
async def prepared_connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            for operation in (m78.upgrade, m79.upgrade, m80.upgrade, m81.upgrade):
                await connection.run_sync(execute, operation)
            await connection.run_sync(seed, schema_metadata(scope_columns=True))
            yield connection
    finally:
        await engine.dispose()


@pytest.fixture
async def migrated_connection(
    prepared_connection: AsyncConnection,
) -> AsyncConnection:
    await prepared_connection.run_sync(execute, m82.upgrade)
    return prepared_connection


async def test_rollback_aborts_before_clearing_post_upgrade_conversation_owner(
    migrated_connection: AsyncConnection,
) -> None:
    # Given: a newly created conversation in the migrated default organization.
    connection = migrated_connection
    table = schema_metadata(scope_columns=True).tables["conversations"]
    agent, owner = (
        await connection.execute(sa.select(table.c.agent_id, table.c.user_id).limit(1))
    ).one()
    key = uuid4()
    await connection.execute(
        sa.insert(table).values(id=key, agent_id=agent, user_id=owner, org_id=ORG, tenant_id=TENANT)
    )
    # When: downgrade is attempted without a surrounding rollback/savepoint.
    with pytest.raises(ValueError, match="m82"):
        await connection.run_sync(execute, m82.downgrade)
    # Then: neither the new owner/scope nor existing migrated data is changed.
    row = (await connection.execute(sa.select(table).where(table.c.id == key))).one()
    assert (row.user_id, row.org_id, row.tenant_id) == (owner, ORG, TENANT)
    assert (await connection.scalars(sa.select(table.c.org_id))).all() == [ORG] * 5


async def test_repeat_upgrade_aborts_before_adopting_new_unscoped_content(
    migrated_connection: AsyncConnection,
) -> None:
    # Given: an unscoped conversation added after completion.
    connection = migrated_connection
    table = schema_metadata(scope_columns=True).tables["conversations"]
    agent = await connection.scalar(sa.select(table.c.agent_id))
    key = uuid4()
    await connection.execute(sa.insert(table).values(id=key, agent_id=agent))
    # When: the already completed migration is rerun.
    with pytest.raises(ValueError, match="m82"):
        await connection.run_sync(execute, m82.upgrade)
    # Then: the migration has not silently adopted the new conversation.
    row = (await connection.execute(sa.select(table).where(table.c.id == key))).one()
    assert (row.user_id, row.org_id, row.tenant_id) == (None, None, None)


@pytest.mark.parametrize("dependency", ["groups", "org_invitations"])
async def test_rollback_aborts_for_new_dependency_with_sqlite_foreign_keys_disabled(
    migrated_connection: AsyncConnection,
    dependency: Literal["groups", "org_invitations"],
) -> None:
    # Given: SQLite's default FK-disabled mode cannot protect new organization data.
    connection = migrated_connection
    metadata = schema_metadata(scope_columns=True)
    assert await connection.scalar(sa.text("PRAGMA foreign_keys")) == 0
    values: dict[str, UUID | str | datetime] = {"id": uuid4(), "org_id": ORG, "tenant_id": TENANT}
    match dependency:
        case "groups":
            values["name"] = "After migration"
        case "org_invitations":
            values.update(
                email="invite@schema.test",
                token_hash="fixture-token",
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        case unexpected:
            assert_never(unexpected)
    await connection.execute(sa.insert(metadata.tables[dependency]).values(**values))
    # When: rollback checks post-migration dependencies explicitly.
    with pytest.raises(ValueError, match=f"m82.*{dependency}"):
        await connection.run_sync(execute, m82.downgrade)
    # Then: the default organization and legacy owners remain intact.
    assert await connection.scalar(sa.select(metadata.tables["organizations"].c.id)) == ORG
    assert (
        await connection.scalars(sa.select(metadata.tables["conversations"].c.org_id))
    ).all() == [ORG] * 4


@pytest.mark.parametrize(
    "change", ["owner", "scope", "credential", "user_org", "membership", "resource_owner"]
)
async def test_rollback_aborts_for_changed_migration_values(
    migrated_connection: AsyncConnection,
    change: Literal["owner", "scope", "credential", "user_org", "membership", "resource_owner"],
) -> None:
    # Given: an original value affected by m82 has been changed after completion.
    connection = migrated_connection
    metadata = schema_metadata(scope_columns=True)
    table = metadata.tables["conversations"]
    owner = await connection.scalar(sa.select(metadata.tables["users"].c.id))
    match change:
        case "owner":
            statement = sa.update(table).values(user_id=None)
        case "scope":
            statement = sa.update(table).values(org_id=None, tenant_id=None)
        case "credential":
            statement = sa.update(metadata.tables["credentials"]).values(scope="personal")
        case "user_org":
            statement = (
                sa.update(metadata.tables["users"])
                .where(
                    metadata.tables["users"].c.id == owner,
                )
                .values(last_active_org_id=None)
            )
        case "resource_owner":
            agents = metadata.tables["agents"]
            original_owner = await connection.scalar(sa.select(agents.c.user_id))
            replacement = await connection.scalar(
                sa.select(metadata.tables["users"].c.id).where(
                    metadata.tables["users"].c.id != original_owner,
                )
            )
            statement = sa.update(agents).values(user_id=replacement)
        case "membership":
            statement = sa.update(metadata.tables["organization_members"]).values(status="removed")
        case unexpected:
            assert_never(unexpected)
    await connection.execute(statement)
    before = (await connection.execute(sa.select(table.c.user_id, table.c.org_id))).all()
    # When: a rollback is attempted without relying on a savepoint for restoration.
    with pytest.raises(ValueError, match="m82"):
        await connection.run_sync(execute, m82.downgrade)
    # Then: no original conversation has been changed by the failed rollback.
    assert (await connection.execute(sa.select(table.c.user_id, table.c.org_id))).all() == before
    assert await connection.scalar(sa.select(metadata.tables["organizations"].c.id)) == ORG


@pytest.mark.parametrize("settings", [{}, {"_migration_m82": {"state": "completed"}}])
async def test_rollback_aborts_when_snapshot_is_missing_or_malformed(
    migrated_connection: AsyncConnection,
    settings: dict[str, JsonValue],
) -> None:
    # Given: reserved provenance is absent or fails its typed migration contract.
    connection = migrated_connection
    metadata = schema_metadata(scope_columns=True)
    await connection.execute(sa.update(metadata.tables["tenants"]).values(settings=settings))
    # When: rollback cannot prove which rows it originally modified.
    with pytest.raises(ValueError, match="m82"):
        await connection.run_sync(execute, m82.downgrade)
    # Then: owner and scope values are retained without any partial rollback.
    assert (
        await connection.scalars(sa.select(metadata.tables["conversations"].c.org_id))
    ).all() == [ORG] * 4


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


@pytest.mark.parametrize("collision", ["tenant", "organization"])
async def test_upgrade_aborts_on_existing_default_uuid_without_provenance(
    prepared_connection: AsyncConnection,
    collision: str,
) -> None:
    # Given: a preexisting default UUID is not owned by m82.
    connection = prepared_connection
    metadata = schema_metadata(scope_columns=True)
    await connection.execute(
        sa.insert(metadata.tables["tenants"]).values(
            id=TENANT if collision == "tenant" else uuid4(),
            name="Existing",
            slug="existing",
            secrets_namespace="existing",
        )
    )
    if collision == "organization":
        tenant = await connection.scalar(sa.select(metadata.tables["tenants"].c.id))
        await connection.execute(
            sa.insert(metadata.tables["organizations"]).values(
                id=ORG,
                tenant_id=tenant,
                name="Existing",
                slug="existing",
            )
        )
    # When: upgrade cannot prove ownership of the default IDs before any writes.
    with pytest.raises(ValueError, match="m82"):
        await connection.run_sync(execute, m82.upgrade)
    # Then: no original conversation owner or scope has been changed.
    rows = (
        await connection.execute(
            sa.select(
                metadata.tables["conversations"].c.user_id,
                metadata.tables["conversations"].c.org_id,
            )
        )
    ).all()
    assert rows == [(None, None)] * 4
    assert (
        await connection.scalar(
            sa.select(sa.func.count()).select_from(
                metadata.tables["tenants"],
            )
        )
        == 1
    )
