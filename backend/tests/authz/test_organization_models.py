"""B-01 registers the reviewed tenant schema in the real application metadata."""

from typing import Protocol, runtime_checkable

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.database import Base
from schema_drafts.org_authz.schema import baseline_metadata


@runtime_checkable
class RevisionOperations(Protocol):
    def upgrade(self) -> None: ...
    def downgrade(self) -> None: ...


CORE_TABLES = frozenset(
    {
        "tenants",
        "tenant_members",
        "tenant_role_grants",
        "organizations",
        "organization_members",
        "organization_role_grants",
        "groups",
        "group_members",
        "org_capability_grants",
        "org_invitations",
        "tenant_domains",
        "sso_connections",
        "user_identities",
    }
)


def test_application_registers_organization_tables_and_active_org_pointer() -> None:
    # Given: the real application metadata used by startup and unit fixtures.
    # When: all registered tables are inspected.
    # Then: organization models and the optional active organization are available.
    assert Base.metadata.tables.keys() >= CORE_TABLES
    column = Base.metadata.tables["users"].c.last_active_org_id
    assert column.nullable
    assert {fk.ondelete for fk in column.foreign_keys} == {"SET NULL"}


def test_alembic_activates_one_m80_head() -> None:
    # Given: the application's actual revision directory.
    config = Config("alembic.ini")
    # When: Alembic resolves the deployable migration graph.
    script = ScriptDirectory.from_config(config)
    # Then: the reviewed first three revisions form the single live head.
    assert script.get_heads() == ["m80_org_capabilities_invitations"]


def test_application_metadata_creates_and_drops_all_organization_tables() -> None:
    # Given: a fresh local SQLite database with real mapped production tables.
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            # When: the application creates its schema without draft imports.
            Base.metadata.create_all(connection)
            # Then: the schema contains every reviewed organization table.
            assert set(inspect(connection).get_table_names()) >= CORE_TABLES
            Base.metadata.drop_all(connection)
    finally:
        engine.dispose()


def test_live_revisions_upgrade_downgrade_and_repeat_on_sqlite() -> None:
    # Given: frozen m77 schema and the deployable revisions selected by Alembic.
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    modules = [
        script.get_revision(name).module
        for name in ("m78_tenants_organizations", "m79_groups", "m80_org_capabilities_invitations")
    ]
    engine = create_engine("sqlite://")
    baseline = baseline_metadata()
    try:
        with engine.begin() as connection:
            baseline.create_all(connection)
            with Operations.context(MigrationContext.configure(connection)):
                for _ in range(2):
                    # When: the live revisions upgrade and downgrade in declared order.
                    for module in modules:
                        assert isinstance(module, RevisionOperations)
                        module.upgrade()
                    assert set(inspect(connection).get_table_names()) >= CORE_TABLES
                    for module in reversed(modules):
                        assert isinstance(module, RevisionOperations)
                        module.downgrade()
                    # Then: all original tables and columns survive without the new pointer.
                    assert set(inspect(connection).get_table_names()) == set(baseline.tables)
                    assert "last_active_org_id" not in {
                        column["name"] for column in inspect(connection).get_columns("users")
                    }
    finally:
        engine.dispose()
