"""All staged drafts compile; data cleanup remains operationally gated."""

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base
from schema_drafts.org_authz.chain import DRAFTS, Draft
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.backfill_fixture import seed
from tests.authz.marketplace_migration_fixture import seed_marketplace


def execute_draft(
    connection: Connection, draft: Draft, *, downgrade: bool = False, disposable: bool = False
) -> None:
    with Operations.context(
        MigrationContext.configure(connection, opts={"org_authz_disposable_validation": disposable})
    ):
        if downgrade:
            draft.downgrade()
        else:
            draft.upgrade()


def test_revision_chain_is_complete_and_has_one_deferred_head() -> None:
    previous = "m77_side_chat_link"
    for draft in DRAFTS:
        assert draft.predecessor == previous
        previous = draft.revision
    assert len(DRAFTS) == 15
    assert previous == "m91_drop_shadow_diffs"


async def test_full_reversible_draft_chain_and_deferred_cleanup_on_sqlite() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            for draft in DRAFTS[:4]:
                await connection.run_sync(execute_draft, draft)
            expected = await connection.run_sync(seed, schema_metadata(scope_columns=True))
            for draft in DRAFTS[4:12]:
                await connection.run_sync(execute_draft, draft)
                if draft.revision == "m84_authz_core":
                    expected_grants = await connection.run_sync(
                        seed_marketplace, schema_metadata(scope_columns=True)
                    )
                if draft.revision == "m85_marketplace_acl_to_grants":
                    grants = schema_metadata().tables["resource_grants"]
                    actual = (
                        (
                            await connection.execute(
                                sa.select(
                                    grants.c.resource_id,
                                    grants.c.relation,
                                    grants.c.principal_type,
                                    grants.c.principal_id,
                                )
                            )
                        )
                        .tuples()
                        .all()
                    )
                    assert set(actual) == expected_grants
                    await connection.run_sync(execute_draft, draft)
                    repeated = (
                        (
                            await connection.execute(
                                sa.select(
                                    grants.c.resource_id,
                                    grants.c.relation,
                                    grants.c.principal_type,
                                    grants.c.principal_id,
                                )
                            )
                        )
                        .tuples()
                        .all()
                    )
                    assert set(repeated) == expected_grants
            tables = await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names())
            assert len(tables) == 85
            # Given: no production observation/backup receipt exists.
            with pytest.raises(ValueError, match="actual org-authz rollout receipt"):
                await connection.run_sync(execute_draft, DRAFTS[12])
            # When: only an explicitly disposable memory DB validates deferred DDL.
            for draft in DRAFTS[12:]:
                await connection.run_sync(execute_draft, draft, disposable=True)
            tables = await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names())
            assert len(tables) == 83
            assert "marketplace_item_acl" not in tables
            assert "authz_shadow_diffs" not in tables
            # Then: cleanup never removed private conversation rows or their owners.
            conversations = schema_metadata(scope_columns=True).tables["conversations"]
            rows = (
                (await connection.execute(sa.select(conversations.c.id, conversations.c.user_id)))
                .tuples()
                .all()
            )
            assert dict(rows) == expected
    finally:
        await engine.dispose()


async def test_reversible_drafts_return_to_original_schema_without_losing_content() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            for draft in DRAFTS[:4]:
                await connection.run_sync(execute_draft, draft)
            await connection.run_sync(seed, schema_metadata(scope_columns=True))
            for draft in DRAFTS[4:12]:
                await connection.run_sync(execute_draft, draft)
            for draft in reversed(DRAFTS[:12]):
                await connection.run_sync(execute_draft, draft, downgrade=True)
            tables = await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names())
            assert set(tables) == set(Base.metadata.tables)
            assert (
                await connection.scalar(
                    sa.select(sa.func.count()).select_from(Base.metadata.tables["conversations"])
                )
                == 4
            )
    finally:
        await engine.dispose()
