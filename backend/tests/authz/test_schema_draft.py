"""A-13 schema contracts are separate from the live m77 application metadata."""

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine

from schema_drafts.org_authz.schema import NEW_TABLES, schema_metadata


async def test_complete_new_schema_compiles_and_creates_on_sqlite() -> None:
    # Given: all 63 baseline tables are available only as copied draft metadata.
    metadata = schema_metadata()
    assert len(NEW_TABLES) == 22
    assert len(metadata.tables) == 85
    # When: a new disposable SQLite database creates all 85 tables.
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            # Then: the draft round-trips all definitions and can be dropped.
            reflected = MetaData()
            await conn.run_sync(reflected.reflect)
            assert set(reflected.tables) == set(metadata.tables)
            await conn.run_sync(metadata.drop_all)
    finally:
        await engine.dispose()


def test_new_constraints_and_indexes_have_stable_names() -> None:
    metadata = schema_metadata()
    for name in NEW_TABLES:
        table = metadata.tables[name]
        assert all(constraint.name for constraint in table.constraints)
        assert all(index.name for index in table.indexes)
        for index in table.indexes:
            if index.dialect_options["postgresql"].get("where") is not None:
                assert index.dialect_options["sqlite"].get("where") is not None


def test_every_org_scoped_new_table_enforces_tenant_pair() -> None:
    metadata = schema_metadata()
    for name in NEW_TABLES:
        table = metadata.tables[name]
        if "org_id" not in table.c:
            continue
        assert "tenant_id" in table.c
        pairs = {tuple(fk.column_keys) for fk in table.foreign_key_constraints}
        assert ("org_id", "tenant_id") in pairs
