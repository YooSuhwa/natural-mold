"""Full drafts start from actual Alembic m77, not merely ORM create_all."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from anyio.to_thread import run_sync
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.database import Base
from schema_drafts.org_authz.chain import DRAFTS
from schema_drafts.org_authz.default_org_backfill import TENANT
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.backfill_fixture import seed
from tests.authz.test_full_draft_chain import execute_draft

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]
HISTORICAL_TABLES = frozenset(
    {
        "_m11_dedup_connection_snapshot",
        "_m11_dedup_tool_remap",
        "_m11_tool_backfill_provenance",
        "_m9_migrated_connections",
        "agent_creation_sessions",
    }
)


async def test_actual_m77_database_accepts_all_drafts_and_fails_closed_under_rls() -> None:
    source = make_url(os.environ["INTEGRATION_DATABASE_URL"])
    database = "moldy_pg_lane_authz_" + uuid4().hex
    role = "authz_actor_" + uuid4().hex
    admin = create_async_engine(
        source.set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT"
    )
    target = source.set(drivername="postgresql+psycopg", database=database)
    engine = create_async_engine(target)
    created = False
    try:
        async with admin.connect() as connection:
            await connection.execute(sa.text(f'CREATE DATABASE "{database}"'))
            created = True
        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "alembic"))
        config.attributes["database_url"] = target.set(
            drivername="postgresql+asyncpg"
        ).render_as_string(hide_password=False)
        await run_sync(command.upgrade, config, "head")
        async with engine.begin() as connection:
            for draft in DRAFTS[:4]:
                await connection.run_sync(execute_draft, draft)
            metadata = schema_metadata(scope_columns=True)
            expected = await connection.run_sync(seed, metadata)
            for draft in DRAFTS[4:12]:
                await connection.run_sync(execute_draft, draft)
            tables = await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names())
            active = set(Base.metadata.tables) | set(schema_metadata().tables)
            extra = set(tables) - {"alembic_version"} - active
            assert extra == HISTORICAL_TABLES
            assert len(set(tables) - {"alembic_version"} - HISTORICAL_TABLES) == 85
            await connection.run_sync(execute_draft, DRAFTS[12], disposable=True)
            await connection.execute(sa.text(f'CREATE ROLE "{role}" NOLOGIN'))
            await connection.execute(sa.text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            await connection.execute(
                sa.text(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                    f'IN SCHEMA public TO "{role}"'
                )
            )
            await connection.execute(sa.text(f'SET LOCAL ROLE "{role}"'))
            # Given: this role is not a table owner and has no BYPASSRLS.
            assert await connection.scalar(sa.text("SELECT count(*) FROM agents")) == 0
            # When: the tenant context is set locally for the transaction.
            await connection.execute(
                sa.text("SELECT set_config('app.tenant_id', :tenant, true)"),
                {"tenant": str(TENANT)},
            )
            assert await connection.scalar(sa.text("SELECT count(*) FROM agents")) == 2
            assert await connection.scalar(sa.text("SELECT count(*) FROM conversations")) == 4
            with pytest.raises(DBAPIError):
                async with connection.begin_nested():
                    await connection.execute(
                        sa.text("UPDATE credentials SET name='forbidden' WHERE tenant_id IS NULL")
                    )
            await connection.execute(sa.text("RESET ROLE"))
            for draft in DRAFTS[13:]:
                await connection.run_sync(execute_draft, draft, disposable=True)
            tables = await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names())
            assert len(set(tables) - {"alembic_version"} - HISTORICAL_TABLES) == 83
            # Then: cleanup keeps every private owner while removing legacy ACL/shadow rows.
            conversations = metadata.tables["conversations"]
            actual = (
                (await connection.execute(sa.select(conversations.c.id, conversations.c.user_id)))
                .tuples()
                .all()
            )
            assert dict(actual) == expected
    finally:
        await engine.dispose()
        if created:
            async with admin.connect() as connection:
                await connection.execute(sa.text(f'DROP DATABASE "{database}" WITH (FORCE)'))
                await connection.execute(sa.text(f'DROP ROLE IF EXISTS "{role}"'))
        await admin.dispose()
