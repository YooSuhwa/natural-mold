"""Live Alembic, PostgreSQL transactional locks, and real saver identity gates."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
import sqlalchemy as sa
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy.exc import OperationalError

from alembic import command
from app.migrations.org_authz_v1 import provenance
from app.migrations.org_authz_v1.contracts import ORG, MigrationContext
from app.migrations.org_authz_v1.metadata import reflect_metadata
from app.migrations.org_authz_v1.snapshot_models import KEY, BackfillSnapshot
from schema_drafts.org_authz.schema import schema_metadata
from tests.authz.org_authz_b02_fixture import IDS, seed
from tests.authz.organization_migration_fixture import organization_lab
from tests.authz.scope_migration_fixture import legacy_rows, relational_contract

pytestmark = pytest.mark.integration


def test_live_alembic_m80_m83_round_trip_preserves_data_and_semantic_constraints() -> None:
    # Given: a disposable database built by the actual complete legacy history.
    with organization_lab() as lab:
        with lab.engine.connect() as connection:
            before = relational_contract(connection)
            inspector = sa.inspect(connection)
            columns = {
                name: tuple(column["name"] for column in inspector.get_columns(name))
                for name in inspector.get_table_names()
                if name != "alembic_version"
            }
        for _ in range(2):
            command.upgrade(lab.config, "m81_org_scope_columns")
            metadata = schema_metadata(scope_columns=True)
            with lab.engine.begin() as connection:
                expected = seed(connection, metadata)
                users = metadata.tables["users"]
                original_users = (
                    connection.execute(sa.select(users).order_by(users.c.id)).mappings().all()
                )
                original_rows = legacy_rows(connection, columns)
            # When: real command.upgrade commits m82 and its following m83 constraints.
            command.upgrade(lab.config, "m83_org_scope_not_null")
            with lab.engine.begin() as connection:
                scoped = reflect_metadata(connection).tables["conversations"]
                assert (
                    dict(
                        connection.execute(sa.select(scoped.c.id, scoped.c.user_id)).tuples().all()
                    )
                    == expected
                )
                assert set(connection.scalars(sa.select(scoped.c.org_id))) == {ORG}
                assert not scoped.c.user_id.nullable
                assert scoped.c.agent_id.nullable
            command.downgrade(lab.config, "m80_org_capabilities_invitations")
            with lab.engine.begin() as connection:
                # Then: actual DDL, user preferences, timestamps and all legacy rows survive.
                assert relational_contract(connection) == before
                assert legacy_rows(connection, columns) == original_rows
                restored = reflect_metadata(connection)
                assert (
                    connection.execute(
                        sa.select(restored.tables["users"]).order_by(restored.tables["users"].c.id)
                    )
                    .mappings()
                    .all()
                    == original_users
                )
                assert (
                    connection.scalar(sa.select(restored.tables["credentials"].c.data_encrypted))
                    == "fixture-ciphertext"
                )
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
                    connection.execute(restored.tables[name].delete())


def test_unresolvable_owner_rolls_back_alembic_version_and_all_backfill_writes() -> None:
    with organization_lab() as lab:
        command.upgrade(lab.config, "m81_org_scope_columns")
        with lab.engine.begin() as connection:
            metadata = schema_metadata(scope_columns=True)
            seed(connection, metadata)
            connection.execute(metadata.tables["skill_builder_sessions"].delete())
        # When: ownership cannot be established for a hidden builder conversation.
        with pytest.raises(ValueError, match="Cannot resolve conversation owners"):
            command.upgrade(lab.config, "m82_default_org_backfill")
        # Then: Alembic's PostgreSQL transaction retains m81 and no company/grants/scopes.
        with lab.engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                "m81_org_scope_columns"
            )
            assert connection.scalar(sa.text("SELECT count(*) FROM tenants")) == 0
            assert connection.scalar(sa.text("SELECT count(*) FROM tenant_members")) == 0
            assert (
                connection.scalar(
                    sa.text("SELECT count(*) FROM conversations WHERE org_id IS NOT NULL")
                )
                == 0
            )


def test_live_downgrade_locks_second_writer_through_verified_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with organization_lab() as lab:
        command.upgrade(lab.config, "m81_org_scope_columns")
        with lab.engine.begin() as connection:
            metadata = schema_metadata(scope_columns=True)
            seed(connection, metadata)
        command.upgrade(lab.config, "m82_default_org_backfill")
        preflight, release = Event(), Event()
        restore = provenance.restore

        def paused_restore(context: MigrationContext, snapshot: BackfillSnapshot) -> None:
            preflight.set()
            assert release.wait(10), "writer probe did not release restore"
            restore(context, snapshot)

        monkeypatch.setattr(provenance, "restore", paused_restore)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(command.downgrade, lab.config, "m81_org_scope_columns")
            try:
                assert preflight.wait(10), "live downgrade never reached verified restore"

                # When: a separate connection attempts an FK child insertion in that window.
                def conflicting_writer() -> None:
                    with lab.engine.begin() as writer:
                        writer.execute(sa.text("SET LOCAL lock_timeout = '300ms'"))
                        writer.execute(
                            metadata.tables["message_events"]
                            .insert()
                            .values(
                                id=uuid4(),
                                conversation_id=IDS["main"],
                                assistant_msg_id=str(uuid4()),
                                events=[],
                            )
                        )

                with pytest.raises(OperationalError, match="lock timeout"):
                    conflicting_writer()
            finally:
                release.set()
            pending.result(timeout=10)
        # Then: the conflicting event did not commit and every original scope is restored.
        with lab.engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT count(*) FROM message_events")) == 0
            assert (
                connection.execute(
                    sa.text("SELECT user_id,org_id,tenant_id FROM conversations")
                ).all()
                == [(None, None, None)] * 4
            )


def test_real_saver_keys_block_rollback_without_recording_private_payload() -> None:
    with organization_lab() as lab:
        command.upgrade(lab.config, "m81_org_scope_columns")
        with lab.engine.begin() as connection:
            seed(connection, schema_metadata(scope_columns=True))
        url = lab.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
        with PostgresSaver.from_conn_string(url) as saver:
            saver.setup()
            checkpoint = empty_checkpoint()
            checkpoint["channel_values"] = {"private": {"payload": "saver-fixture-secret"}}
            checkpoint["channel_versions"] = {"private": "1"}
            saved = saver.put(
                {"configurable": {"thread_id": "private-thread", "checkpoint_ns": ""}},
                checkpoint,
                {"source": "input", "step": -1},
                {"private": "1"},
            )
            saver.put_writes(saved, [("private", "saver-fixture-secret")], "original-task")
            command.upgrade(lab.config, "m82_default_org_backfill")
            with lab.engine.connect() as connection:
                metadata = reflect_metadata(connection)
                settings = connection.scalar(sa.select(metadata.tables["tenants"].c.settings))
                assert isinstance(settings, dict)
                snapshot = BackfillSnapshot.model_validate(settings[KEY])
                assert {item.name for item in snapshot.dependencies} >= {
                    "checkpoints",
                    "checkpoint_blobs",
                    "checkpoint_writes",
                    "checkpoint_migrations",
                }
                assert "saver-fixture-secret" not in snapshot.model_dump_json()
            # When: a real saver adds a pending task identity after the migration.
            saver.put_writes(saved, [("private", "saver-fixture-secret")], "new-task")
            with pytest.raises(ValueError, match="m82.*checkpoint_writes"):
                command.downgrade(lab.config, "m81_org_scope_columns")
            # Then: rejection leaves the Alembic revision and current company scopes intact.
            with lab.engine.connect() as connection:
                assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == (
                    "m82_default_org_backfill"
                )
                assert set(connection.scalars(sa.text("SELECT org_id FROM conversations"))) == {ORG}
