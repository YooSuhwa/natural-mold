"""Actual PostgreSQL DDL, FK denial and reversible initial migration drafts."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base
from schema_drafts.org_authz import m78_tenants_organizations as m78
from schema_drafts.org_authz import m79_groups as m79
from schema_drafts.org_authz import m80_org_capabilities_invitations as m80
from schema_drafts.org_authz import m81_org_scope_columns as m81
from schema_drafts.org_authz import m82_default_org_backfill as m82
from schema_drafts.org_authz import m83_org_scope_not_null as m83
from schema_drafts.org_authz import m84_authz_core as m84
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.backfill_fixture import seed
from tests.authz.test_schema_draft_migrations import execute

pytestmark = pytest.mark.integration


async def test_postgres_initial_drafts_and_cross_tenant_composite_fk() -> None:
    schema = "authz_schema_" + uuid4().hex
    engine = create_async_engine(os.environ["INTEGRATION_DATABASE_URL"])
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await conn.run_sync(Base.metadata.create_all)
            for operation in (m78.upgrade, m79.upgrade, m80.upgrade, m81.upgrade):
                await conn.run_sync(execute, operation)
            metadata = schema_metadata(scope_columns=True)
            tenant_a, tenant_b, org_a, user = uuid4(), uuid4(), uuid4(), uuid4()
            await conn.execute(
                insert(metadata.tables["users"]).values(
                    id=user, email="schema@example.test", name="Schema"
                )
            )
            await conn.execute(
                insert(metadata.tables["tenants"]),
                [
                    {"id": tenant_a, "name": "A", "slug": "a", "secrets_namespace": "a"},
                    {"id": tenant_b, "name": "B", "slug": "b", "secrets_namespace": "b"},
                ],
            )
            await conn.execute(
                insert(metadata.tables["organizations"]).values(
                    id=org_a, tenant_id=tenant_a, name="A", slug="a"
                )
            )
            # Given: the organization belongs to A, but a membership claims B.
            with pytest.raises(IntegrityError):
                async with conn.begin_nested():
                    await conn.execute(
                        insert(metadata.tables["organization_members"]).values(
                            org_id=org_a, tenant_id=tenant_b, user_id=user
                        )
                    )
            # Then: the correct tenant pair is accepted, and the draft reverses.
            await conn.execute(
                insert(metadata.tables["organization_members"]).values(
                    org_id=org_a, tenant_id=tenant_a, user_id=user
                )
            )
            expected = await conn.run_sync(seed, metadata)
            for _ in range(2):
                await conn.run_sync(execute, m82.upgrade)
                conversation = metadata.tables["conversations"]
                rows = (
                    (await conn.execute(select(conversation.c.id, conversation.c.user_id)))
                    .tuples()
                    .all()
                )
                assert dict(rows) == expected
            await conn.run_sync(execute, m83.upgrade)
            await conn.run_sync(execute, m84.upgrade)
            async with conn.begin_nested() as deletion:
                agents = metadata.tables["agents"]
                with pytest.raises(IntegrityError):
                    async with conn.begin_nested():
                        await conn.execute(
                            delete(metadata.tables["users"]).where(
                                metadata.tables["users"].c.is_super_user.is_(True)
                            )
                        )
                await conn.execute(delete(agents).where(agents.c.runtime_profile == "standard"))
                assert await conn.scalar(select(func.count()).select_from(conversation)) == 4
                assert (
                    await conn.scalar(
                        select(func.count())
                        .select_from(conversation)
                        .where(conversation.c.agent_id.is_(None))
                    )
                    == 3
                )
                with pytest.raises(ValueError, match="restoring their agents"):
                    await conn.run_sync(execute, m83.downgrade)
                await deletion.rollback()
            await conn.run_sync(execute, m84.downgrade)
            await conn.run_sync(execute, m83.downgrade)
            await conn.run_sync(execute, m82.downgrade)
            for operation in (m81.downgrade, m80.downgrade, m79.downgrade, m78.downgrade):
                await conn.run_sync(execute, operation)
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        await engine.dispose()


async def test_postgres_complete_new_schema_creates_all_tables() -> None:
    schema = "authz_complete_" + uuid4().hex
    engine = create_async_engine(os.environ["INTEGRATION_DATABASE_URL"])
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            metadata = schema_metadata()
            await conn.run_sync(metadata.create_all)
            count = await conn.scalar(
                text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = :schema"),
                {"schema": schema},
            )
            assert count == 85
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        await engine.dispose()
