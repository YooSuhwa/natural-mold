"""Recovery enrolment and durable tool receipts on pre-upgrade conversations."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import Base
from app.models.conversation_run import ConversationRunToolReceipt

_TABLE = "conversation_run_tool_receipts"


def _migration() -> ModuleType:
    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "alembic"))
    revision = scripts.get_revision("i43_chat_recovery")
    assert revision is not None
    return revision.module


def _prior_schema(db: Connection) -> None:
    db.exec_driver_sql("CREATE TABLE tenants (id CHAR(32) PRIMARY KEY)")
    db.exec_driver_sql(
        "CREATE TABLE organizations (id CHAR(32) PRIMARY KEY, tenant_id CHAR(32) NOT NULL, "
        "UNIQUE(id, tenant_id))"
    )
    db.exec_driver_sql(
        "CREATE TABLE conversation_runs (id CHAR(32) PRIMARY KEY, tenant_id CHAR(32), "
        "org_id CHAR(32), worker_instance_id VARCHAR(80))"
    )


def test_upgrade_retains_legacy_runs_and_scopes_receipts() -> None:
    migration = _migration()
    engine = sa.create_engine("sqlite://")
    tenant_id, other_tenant_id = uuid4(), uuid4()
    org_id, other_org_id = uuid4(), uuid4()
    run, second_run = uuid4(), uuid4()
    moment = datetime.now(UTC).isoformat()
    try:
        with engine.begin() as db:
            db.exec_driver_sql("PRAGMA foreign_keys = ON")
            _prior_schema(db)
            db.exec_driver_sql(
                "INSERT INTO tenants VALUES (?)", [(tenant_id.hex,), (other_tenant_id.hex,)]
            )
            db.exec_driver_sql(
                "INSERT INTO organizations VALUES (?, ?)",
                [(org_id.hex, tenant_id.hex), (other_org_id.hex, tenant_id.hex)],
            )
            db.exec_driver_sql(
                "INSERT INTO conversation_runs VALUES (?, ?, ?, 'old-worker')",
                [(run.hex, tenant_id.hex, org_id.hex), (second_run.hex, tenant_id.hex, org_id.hex)],
            )
            with Operations.context(MigrationContext.configure(db)):
                migration.upgrade()
            rows = db.exec_driver_sql(
                "SELECT owner_generation, recovery_attempts, owner_token, recovery_runtime_digest "
                "FROM conversation_runs"
            ).all()
            assert rows == [(0, 0, None, None)] * 2
            assert ConversationRunToolReceipt.__tablename__ == _TABLE
            inspector = sa.inspect(db)
            columns = {c["name"]: c["nullable"] for c in inspector.get_columns(_TABLE)}
            assert columns == {c.name: c.nullable for c in Base.metadata.tables[_TABLE].columns}
            assert inspector.get_pk_constraint(_TABLE)["constrained_columns"] == [
                "run_id",
                "tool_call_id",
            ]
            insert = sa.text(
                "INSERT INTO conversation_run_tool_receipts "
                "(run_id, tool_call_id, tenant_id, org_id, owner_generation, tool_name, "
                "arguments_sha256, effect_kind, status, result_json, created_at, updated_at) "
                "VALUES (:run, :call, :tenant, :org, :generation, 'search', :digest, "
                ":effect, :status, NULL, :created, :updated)"
            )
            values = {
                "run": run.hex,
                "call": "call-1",
                "tenant": tenant_id.hex,
                "org": org_id.hex,
                "generation": 1,
                "digest": "a" * 64,
                "effect": "read",
                "status": "started",
                "created": moment,
                "updated": moment,
            }
            for invalid in (
                {"org": other_org_id.hex},
                {"tenant": other_tenant_id.hex},
                {"generation": 0},
                {"status": "unknown"},
                {"effect": "unknown"},
            ):
                with pytest.raises(IntegrityError):
                    db.execute(insert, {**values, **invalid})

            db.execute(insert, values)
            with pytest.raises(IntegrityError):
                db.execute(insert, {**values, "generation": 2})
            db.execute(insert, {**values, "run": second_run.hex})
            db.exec_driver_sql("DELETE FROM conversation_runs WHERE id = ?", (run.hex,))
            surviving = db.exec_driver_sql(
                "SELECT run_id FROM conversation_run_tool_receipts"
            ).scalar_one()
            assert UUID(surviving) == second_run
            with Operations.context(MigrationContext.configure(db)):
                migration.downgrade()
            inspector = sa.inspect(db)
            assert not inspector.has_table(_TABLE)
            assert {c["name"] for c in inspector.get_columns("conversation_runs")} == {
                "id",
                "tenant_id",
                "org_id",
                "worker_instance_id",
            }
            assert (
                db.exec_driver_sql("SELECT worker_instance_id FROM conversation_runs").scalar_one()
                == "old-worker"
            )
    finally:
        engine.dispose()


def test_postgres_migration_renders_receipt_tenant_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _migration()
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    ddl = StringIO()
    with Operations.context(
        MigrationContext.configure(
            dialect_name="postgresql", opts={"as_sql": True, "output_buffer": ddl}
        )
    ):
        migration.upgrade()
        migration.downgrade()
    statements = ddl.getvalue()
    assert f"CREATE POLICY tenant_boundary ON {_TABLE}" in statements
    assert "current_setting('app.tenant_id'" in statements
    assert f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY" in statements
    assert f"DROP TABLE {_TABLE}" in statements
