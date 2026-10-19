import pytest
import sqlalchemy as sa

from app.migrations.org_authz_v1 import provenance, snapshot_io
from app.migrations.org_authz_v1.contracts import TENANT
from app.migrations.org_authz_v1.m82_backfill import backfill, reverse_backfill
from app.migrations.org_authz_v1.metadata import migration_context
from app.migrations.org_authz_v1.snapshot_models import KEY
from tests.authz.org_authz_b02_fixture import IDS, prepare, seed


def test_clean_snapshot_round_trip_restores_exact_values() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        metadata = prepare(connection)
        seed(connection, metadata)
        context = migration_context(connection)
        before = provenance.capture(context)
        backfill(connection)
        snapshot = snapshot_io.load(context)
        assert snapshot is not None
        assert snapshot.version == 2
        assert snapshot.revision == "m82_default_org_backfill"
        assert snapshot.state == "completed"
        assert "fixture-ciphertext" not in snapshot.model_dump_json()
        reverse_backfill(connection)
        assert provenance.capture(context) == before
    engine.dispose()


def test_dependency_drift_refuses_before_first_restore_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = sa.create_engine("sqlite://")
    writes: list[str] = []
    with engine.begin() as connection:
        metadata = prepare(connection)
        seed(connection, metadata)
        children = sa.MetaData()
        sa.Table("conversations", children, sa.Column("id", sa.Uuid(), primary_key=True))
        first = sa.Table(
            "child_one",
            children,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id")),
            sa.Column("payload", sa.String()),
        )
        second = sa.Table(
            "child_two",
            children,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("parent_id", sa.Integer(), sa.ForeignKey("child_one.id")),
        )
        third = sa.Table(
            "child_three",
            children,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("parent_id", sa.Integer(), sa.ForeignKey("child_two.id")),
            sa.Column("payload", sa.String()),
        )
        children.create_all(connection, tables=[first, second, third])
        connection.execute(
            first.insert().values(
                id=1, conversation_id=IDS["main"], payload="PRIVATE_CHILD_PAYLOAD"
            )
        )
        connection.execute(second.insert().values(id=2, parent_id=1))
        backfill(connection)
        snapshot = snapshot_io.load(migration_context(connection))
        assert snapshot is not None
        assert "child_three" in {item.name for item in snapshot.dependencies}
        assert "PRIVATE_CHILD_PAYLOAD" not in snapshot.model_dump_json()
        connection.execute(third.insert().values(id=3, parent_id=2, payload="new"))
        monkeypatch.setattr(provenance, "restore", lambda *_: writes.append("restore"))
        with pytest.raises(
            ValueError, match="dependent identity/reference state changed: child_three"
        ):
            reverse_backfill(connection)
        assert writes == []
        assert (
            connection.scalar(sa.select(sa.func.count()).select_from(metadata.tables["tenants"]))
            == 1
        )
    engine.dispose()


@pytest.mark.parametrize("raw", [None, {"version": 1}, {"revision": "wrong"}])
def test_malformed_or_missing_provenance_fails_closed(raw: dict[str, int | str] | None) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        metadata = prepare(connection)
        seed(connection, metadata)
        backfill(connection)
        tenants = metadata.tables["tenants"]
        connection.execute(
            tenants.update()
            .where(tenants.c.id == TENANT)
            .values(settings={} if raw is None else {KEY: raw})
        )
        with pytest.raises(ValueError, match="snapshot (unavailable|malformed)"):
            reverse_backfill(connection)
    engine.dispose()


def test_keyless_physical_dependent_aborts_before_creation() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        metadata = prepare(connection)
        seed(connection, metadata)
        connection.exec_driver_sql(
            "CREATE TABLE keyless (conversation_id CHAR(32) "
            "REFERENCES conversations(id), payload TEXT)"
        )
        with pytest.raises(ValueError, match="no stable primary identity: keyless"):
            backfill(connection)
        assert (
            connection.scalar(sa.select(sa.func.count()).select_from(metadata.tables["tenants"]))
            == 0
        )
    engine.dispose()


def test_late_default_reference_refuses_before_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = sa.create_engine("sqlite://")
    writes: list[str] = []
    with engine.begin() as connection:
        metadata = prepare(connection)
        seed(connection, metadata)
        backfill(connection)
        connection.exec_driver_sql(
            "CREATE TABLE loose_scope (id INTEGER PRIMARY KEY, tenant_id CHAR(32))"
        )
        connection.exec_driver_sql("INSERT INTO loose_scope VALUES (1, ?)", (TENANT.hex,))
        monkeypatch.setattr(provenance, "restore", lambda *_: writes.append("restore"))
        with pytest.raises(ValueError, match="untracked dependencies: loose_scope"):
            reverse_backfill(connection)
        assert writes == []
    engine.dispose()


def test_opaque_checkpoint_identity_and_payload_exclusion() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        metadata = prepare(connection)
        seed(connection, metadata)
        connection.exec_driver_sql(
            "CREATE TABLE checkpoints (thread_id TEXT, checkpoint_ns TEXT, "
            "checkpoint_id TEXT, parent_checkpoint_id TEXT, checkpoint TEXT, "
            "PRIMARY KEY(thread_id, checkpoint_ns, checkpoint_id))"
        )
        connection.exec_driver_sql(
            "INSERT INTO checkpoints VALUES ('opaque.thread', '', 'opaque.checkpoint', "
            "NULL, 'PRIVATE_CHECKPOINT_PAYLOAD')"
        )
        backfill(connection)
        snapshot = snapshot_io.load(migration_context(connection))
        assert snapshot is not None
        checkpoint = next(item for item in snapshot.dependencies if item.name == "checkpoints")
        assert checkpoint.expected == [
            {
                "thread_id": "opaque.thread",
                "checkpoint_ns": "",
                "checkpoint_id": "opaque.checkpoint",
                "parent_checkpoint_id": None,
            }
        ]
        assert "PRIVATE_CHECKPOINT_PAYLOAD" not in snapshot.model_dump_json()
        connection.exec_driver_sql("UPDATE checkpoints SET parent_checkpoint_id = 'changed'")
        with pytest.raises(
            ValueError, match="dependent identity/reference state changed: checkpoints"
        ):
            reverse_backfill(connection)
    engine.dispose()


def test_default_identity_collision_is_preserved() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        metadata = prepare(connection)
        seed(connection, metadata)
        tenants = metadata.tables["tenants"]
        connection.execute(
            tenants.insert().values(
                id=TENANT, name="Existing", slug="existing", secrets_namespace="existing"
            )
        )
        with pytest.raises(ValueError, match="default tenant collision"):
            backfill(connection)
        assert (
            connection.scalar(sa.select(tenants.c.name).where(tenants.c.id == TENANT)) == "Existing"
        )
    engine.dispose()


def test_referenced_natural_key_drift_refuses_before_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = sa.create_engine("sqlite://")
    writes: list[str] = []
    with engine.begin() as connection:
        metadata = prepare(connection)
        seed(connection, metadata)
        connection.exec_driver_sql(
            "CREATE TABLE natural_parent (id INTEGER PRIMARY KEY, conversation_id CHAR(32) "
            "REFERENCES conversations(id), natural_key TEXT UNIQUE, payload TEXT)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE natural_child (id INTEGER PRIMARY KEY, parent_key TEXT "
            "REFERENCES natural_parent(natural_key))"
        )
        connection.exec_driver_sql(
            "INSERT INTO natural_parent VALUES (1, ?, 'old', 'PRIVATE_NATURAL_PAYLOAD')",
            (IDS["main"].hex,),
        )
        connection.exec_driver_sql("INSERT INTO natural_child VALUES (2, 'old')")
        backfill(connection)
        snapshot = snapshot_io.load(migration_context(connection))
        assert snapshot is not None
        parent = next(item for item in snapshot.dependencies if item.name == "natural_parent")
        assert parent.columns == ("id", "conversation_id", "natural_key")
        assert "PRIVATE_NATURAL_PAYLOAD" not in snapshot.model_dump_json()
        connection.exec_driver_sql("UPDATE natural_parent SET natural_key = 'new'")
        monkeypatch.setattr(provenance, "restore", lambda *_: writes.append("restore"))
        with pytest.raises(
            ValueError, match="dependent identity/reference state changed: natural_parent"
        ):
            reverse_backfill(connection)
        assert writes == []
    engine.dispose()
