"""The live m81–m83 chain preserves ownership and round-trips existing data."""

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.migrations.org_authz_v1.contracts import M81_SCOPED, ORG, SHARED, TENANT
from app.migrations.org_authz_v1.metadata import reflect_metadata
from app.migrations.org_authz_v1.snapshot_models import KEY
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.org_authz_b02_fixture import IDS, seed
from tests.authz.scope_migration_fixture import execute, prepare_m80, relational_contract


def test_live_scope_chain_round_trips_twice_without_content_or_timestamp_changes() -> None:
    # Given: the live first three organization revisions over a frozen m77 schema.
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            prepare_m80(connection)
            original_schema = relational_contract(connection)
            for _ in range(2):
                execute(connection, "m81_org_scope_columns")
                metadata = schema_metadata(scope_columns=True)
                expected = seed(connection, metadata)
                before = connection.execute(
                    sa.select(metadata.tables["users"]).order_by(metadata.tables["users"].c.id)
                ).all()
                # When: the deployable m82 and m83 revisions are executed.
                execute(connection, "m82_default_org_backfill")
                execute(connection, "m83_org_scope_not_null")
                reflected = reflect_metadata(connection)
                conversations = reflected.tables["conversations"]
                assert (
                    dict(
                        connection.execute(sa.select(conversations.c.id, conversations.c.user_id))
                        .tuples()
                        .all()
                    )
                    == expected
                )
                assert set(connection.scalars(sa.select(conversations.c.org_id))) == {ORG}
                assert set(connection.scalars(sa.select(conversations.c.tenant_id))) == {TENANT}
                assert set(connection.scalars(sa.select(conversations.c.agent_name_snapshot))) == {
                    "Fixture"
                }
                settings = connection.scalar(sa.select(reflected.tables["tenants"].c.settings))
                assert isinstance(settings, dict)
                assert KEY in settings
                assert "fixture-ciphertext" not in str(settings)
                for name in SHARED:
                    owner = "owner_user_id" if name == "marketplace_items" else "user_id"
                    foreign_keys = sa.inspect(connection).get_foreign_keys(name)
                    assert (
                        next(fk for fk in foreign_keys if fk["constrained_columns"] == [owner])
                        .get("options", {})
                        .get("ondelete")
                        == "RESTRICT"
                    )
                for name in M81_SCOPED:
                    columns = {c["name"]: c for c in sa.inspect(connection).get_columns(name)}
                    if "is_system" not in columns:
                        assert not columns["org_id"]["nullable"]
                execute(connection, "m83_org_scope_not_null", downgrade=True)
                execute(connection, "m82_default_org_backfill", downgrade=True)
                assert (
                    connection.execute(
                        sa.select(metadata.tables["users"]).order_by(metadata.tables["users"].c.id)
                    ).all()
                    == before
                )
                execute(connection, "m81_org_scope_columns", downgrade=True)
                # Then: semantic m80 schema and original rows remain, and another cycle works.
                assert relational_contract(connection) == original_schema
                for name in (
                    "skill_builder_sessions",
                    "agent_api_threads",
                    "conversations",
                    "agent_deployments",
                    "agents",
                    "credentials",
                    "models",
                    "users",
                ):
                    connection.execute(reflect_metadata(connection).tables[name].delete())
    finally:
        engine.dispose()


def test_m83_rejects_missing_private_scope_and_orphaned_history_downgrade() -> None:
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            prepare_m80(connection)
            execute(connection, "m81_org_scope_columns")
            seed(connection, schema_metadata(scope_columns=True))
            execute(connection, "m82_default_org_backfill")
            execute(connection, "m83_org_scope_not_null")
            metadata = reflect_metadata(connection)
            conversations = metadata.tables["conversations"]
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(conversations.update().values(org_id=None))
            connection.execute(
                conversations.update()
                .where(conversations.c.id == IDS["main"])
                .values(agent_id=None)
            )
            before = relational_contract(connection)
            # When: an archived/deleted agent's history cannot fit the legacy non-null FK.
            with pytest.raises(ValueError, match="restoring their agents"):
                execute(connection, "m83_org_scope_not_null", downgrade=True)
            # Then: rejection precedes every downgrade DDL mutation.
            assert relational_contract(connection) == before
    finally:
        engine.dispose()
