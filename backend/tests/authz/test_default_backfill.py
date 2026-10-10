"""m82 preserves API callers, inherited side-chat ownership and hidden-session users."""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base
from schema_drafts.org_authz import m78_tenants_organizations as m78
from schema_drafts.org_authz import m79_groups as m79
from schema_drafts.org_authz import m80_org_capabilities_invitations as m80
from schema_drafts.org_authz import m81_org_scope_columns as m81
from schema_drafts.org_authz import m82_default_org_backfill as m82
from schema_drafts.org_authz import m83_org_scope_not_null as m83
from schema_drafts.org_authz.default_org_backfill import ORG
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.backfill_fixture import seed
from tests.authz.test_schema_draft_migrations import execute


async def test_backfill_preserves_creator_precedence_and_is_idempotent() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for operation in (m78.upgrade, m79.upgrade, m80.upgrade, m81.upgrade):
                await conn.run_sync(execute, operation)
            metadata = schema_metadata(scope_columns=True)
            expected = await conn.run_sync(seed, metadata)
            for _ in range(2):
                # When: the full data migration is run, including its repeat.
                await conn.run_sync(execute, m82.upgrade)
                conversations = metadata.tables["conversations"]
                rows = (
                    (await conn.execute(sa.select(conversations.c.id, conversations.c.user_id)))
                    .tuples()
                    .all()
                )
                # Then: the API/side caller and hidden-session user retain their private data.
                assert dict(rows) == expected
                assert (
                    await conn.scalar(
                        sa.select(sa.func.count()).select_from(metadata.tables["organizations"])
                    )
                    == 1
                )
                assert (
                    await conn.scalar(
                        sa.select(sa.func.count()).select_from(
                            metadata.tables["organization_role_grants"]
                        )
                    )
                    == 3
                )
            credentials = metadata.tables["credentials"]
            row = (
                await conn.execute(
                    sa.select(credentials.c.scope, credentials.c.org_id, credentials.c.tenant_id)
                )
            ).one()
            assert tuple(row) == ("platform", None, None)
            assert (await conn.scalars(sa.select(conversations.c.org_id))).all() == [ORG] * 4
            await conn.run_sync(execute, m83.upgrade)
            await conn.run_sync(execute, m83.downgrade)
            await conn.run_sync(execute, m82.downgrade)
            assert (
                await conn.scalar(
                    sa.select(sa.func.count()).select_from(metadata.tables["tenants"])
                )
                == 0
            )
            assert (await conn.scalars(sa.select(conversations.c.user_id))).all() == [None] * 4
            assert await conn.scalar(sa.select(sa.func.count()).select_from(conversations)) == 4
    finally:
        await engine.dispose()


async def test_unresolved_hidden_session_aborts_instead_of_assigning_agent_owner() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for operation in (m78.upgrade, m79.upgrade, m80.upgrade, m81.upgrade):
                await conn.run_sync(execute, operation)
            metadata = schema_metadata(scope_columns=True)
            await conn.run_sync(seed, metadata)
            await conn.execute(sa.delete(metadata.tables["skill_builder_sessions"]))
            # Given: a hidden conversation lacks its source session owner.
            with pytest.raises(ValueError, match="Cannot resolve conversation owners"):
                async with conn.begin_nested():
                    await conn.run_sync(execute, m82.upgrade)
            # Then: the failed migration transaction has not committed default membership.
            assert (
                await conn.scalar(
                    sa.select(sa.func.count()).select_from(metadata.tables["tenant_members"])
                )
                == 0
            )
    finally:
        await engine.dispose()
