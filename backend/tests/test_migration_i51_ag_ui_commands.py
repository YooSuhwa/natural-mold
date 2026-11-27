"""Verify the AG-UI receipt migration against existing conversations and both targets."""

from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError

from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.services.platform.tenant_data_scope import secret_columns


def test_receipt_targets_survive_individual_deletion_and_follow_conversation() -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    revisions = ScriptDirectory.from_config(config)
    revision = revisions.get_revision("i51_ag_ui_commands")
    guard = revisions.get_revision("i52_ag_ui_queue_claim_guard")
    assert guard and guard.down_revision == "i51_ag_ui_commands"
    assert revision and revision.down_revision == "i50_chat_model_attempts"
    migration = revision.module
    table = AgUiCommandReceipt.__table__
    assert isinstance(table, sa.Table)
    user, tenant, org, thread, run, queued, spare = [uuid4() for _ in range(7)]
    engine = sa.create_engine("sqlite://")
    with engine.connect() as db:
        db.exec_driver_sql("PRAGMA foreign_keys=ON")
        for name, value in (("users", user), ("tenants", tenant), ("conversations", thread)):
            parent = sa.Table(name, sa.MetaData(), sa.Column("id", sa.Uuid(), primary_key=True))
            parent.create(db)
            db.execute(parent.insert().values(id=value))
        db.exec_driver_sql(
            "CREATE TABLE organizations (id CHAR(32), tenant_id CHAR(32), "
            "PRIMARY KEY(id,tenant_id))"
        )
        db.exec_driver_sql("INSERT INTO organizations VALUES (?,?)", (org.hex, tenant.hex))
        for name, values in (
            ("conversation_runs", [run, spare]),
            ("conversation_run_inputs", [queued]),
        ):
            meta = sa.MetaData()
            sa.Table("conversations", meta, autoload_with=db)
            parent = sa.Table(
                name,
                meta,
                sa.Column("id", sa.Uuid(), primary_key=True),
                sa.Column(
                    "conversation_id",
                    sa.Uuid(),
                    sa.ForeignKey("conversations.id", ondelete="CASCADE"),
                ),
            )
            parent.create(db)
            db.execute(
                parent.insert(), [{"id": value, "conversation_id": thread} for value in values]
            )

        with Operations.context(MigrationContext.configure(db)):
            migration.upgrade()
            assert {c["name"]: c["nullable"] for c in sa.inspect(db).get_columns(table.name)} == {
                c.name: c.nullable for c in table.c
            }
            insert = table.insert().values(
                user_id=user,
                tenant_id=tenant,
                org_id=org,
                conversation_id=thread,
                request_digest="1" * 64,
            )
            db.execute(
                insert.values(public_run_id="run/?#", run_id=run, delivery_encrypted="sealed")
            )
            db.execute(insert.values(public_run_id="queue", input_id=queued))
            db.commit()
            guard.module.upgrade()
            guard.module.downgrade()
            assert db.scalar(sa.select(sa.func.count()).select_from(table)) == 2
            for invalid in (
                {"public_run_id": "no-target"},
                {"public_run_id": "two-targets", "run_id": spare, "input_id": queued},
                {"public_run_id": "", "run_id": spare},
                {"public_run_id": "x" * 201, "run_id": spare},
                {"public_run_id": "digest", "run_id": spare, "request_digest": "x"},
                {"public_run_id": "another", "run_id": run},
                {"public_run_id": "another", "input_id": queued},
                {"public_run_id": "run/?#", "run_id": spare},
                {"public_run_id": "queue-payload", "input_id": uuid4(), "delivery_encrypted": "x"},
            ):
                with pytest.raises(IntegrityError):
                    db.execute(insert.values(**invalid))
                db.rollback()
            for name, value in (("conversation_runs", run), ("conversation_run_inputs", queued)):
                parent = sa.Table(name, sa.MetaData(), autoload_with=db)
                db.execute(parent.delete().where(parent.c.id == value.hex))
                with pytest.raises(IntegrityError):
                    db.exec_driver_sql("COMMIT")
                db.rollback()
            db.exec_driver_sql("DELETE FROM conversations WHERE id=?", (thread.hex,))
            db.commit()
            assert db.scalar(sa.select(sa.func.count()).select_from(table)) == 0
            migration.downgrade()
            assert not sa.inspect(db).has_table(table.name)
            migration.upgrade()
            assert sa.inspect(db).has_table(table.name)
    engine.dispose()


def test_delivery_is_omitted_from_normal_reads_and_tenant_exports() -> None:
    mapped = sa.inspect(AgUiCommandReceipt)
    assert mapped.attrs.delivery_encrypted.deferred and mapped.attrs.delivery_encrypted.raiseload
    assert isinstance(mapped.local_table, sa.Table)
    assert "delivery_encrypted" in secret_columns(mapped.local_table)
