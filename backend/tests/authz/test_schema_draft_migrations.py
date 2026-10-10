"""Execute m78–m80 drafts through real Alembic Operations without activating head."""

from collections.abc import Callable

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import Base
from schema_drafts.org_authz import m78_tenants_organizations as m78
from schema_drafts.org_authz import m79_groups as m79
from schema_drafts.org_authz import m80_org_capabilities_invitations as m80
from schema_drafts.org_authz import m81_org_scope_columns as m81


def execute(connection: Connection, operation: Callable[[], None]) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        operation()


async def test_initial_drafts_upgrade_downgrade_and_repeat_on_sqlite() -> None:
    # Given: the existing baseline schema, not a synthetic subset of its tables.
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for _ in range(2):
                # When: staged Alembic draft upgrades run in their declared order.
                for operation in (m78.upgrade, m79.upgrade, m80.upgrade, m81.upgrade):
                    await conn.run_sync(execute, operation)
                tables = await conn.run_sync(
                    lambda connection: inspect(connection).get_table_names()
                )
                assert len(tables) == 76
                columns = await conn.run_sync(
                    lambda connection: inspect(connection).get_columns("users")
                )
                assert "last_active_org_id" in {column["name"] for column in columns}
                # Then: all reversible drafts return exactly to the baseline.
                for operation in (m81.downgrade, m80.downgrade, m79.downgrade, m78.downgrade):
                    await conn.run_sync(execute, operation)
                tables = await conn.run_sync(
                    lambda connection: inspect(connection).get_table_names()
                )
                assert set(tables) == set(Base.metadata.tables)
    finally:
        await engine.dispose()
