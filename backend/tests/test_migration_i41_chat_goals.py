"""Exercise the migration's physical constraints independently of ORM create_all."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

from app.models.conversation_goal import ConversationGoal, GoalAttempt, GoalTransitionIntent


def test_goal_schema_enforces_deduplication_and_preserves_existing_conversations() -> None:
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "i41_chat_goals.py"
    spec = importlib.util.spec_from_file_location("i41_goal_migration_test", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    user, org, tenant, conversation, goal, input_id = [uuid4() for _ in range(6)]
    runs = [uuid4() for _ in range(4)]
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys = ON")
        for table_name, row_ids in (
            ("users", [user]),
            ("tenants", [tenant]),
            ("conversations", [conversation]),
            ("conversation_runs", runs),
            ("conversation_run_inputs", [input_id]),
        ):
            table = sa.Table(
                table_name, sa.MetaData(), sa.Column("id", sa.CHAR(32), primary_key=True)
            )
            table.create(connection)
            connection.execute(table.insert(), [{"id": value.hex} for value in row_ids])
        connection.exec_driver_sql(
            "CREATE TABLE organizations (id CHAR(32),tenant_id CHAR(32),PRIMARY KEY(id,tenant_id))"
        )
        connection.exec_driver_sql("INSERT INTO organizations VALUES (?,?)", (org.hex, tenant.hex))

        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            inspector = sa.inspect(connection)
            for model in (ConversationGoal, GoalAttempt, GoalTransitionIntent):
                columns = {
                    c["name"]: c["nullable"] for c in inspector.get_columns(model.__tablename__)
                }
                assert columns == {c.name: c.nullable for c in model.__table__.columns}

            insert_goal = sa.insert(ConversationGoal).values(
                conversation_id=conversation,
                org_id=org,
                tenant_id=tenant,
                user_id=user,
                title="evidence",
                criteria=[],
            )
            connection.execute(insert_goal.values(id=goal))
            with pytest.raises(IntegrityError):
                connection.execute(insert_goal.values(id=uuid4()))

            insert_attempt = sa.insert(GoalAttempt).values(goal_id=goal, goal_version=1)
            connection.execute(
                insert_attempt.values(
                    run_id=runs[0], followup_ordinal=1, followup_input_id=input_id
                )
            )
            for duplicate in (
                {"run_id": runs[0]},
                {"run_id": runs[1], "followup_ordinal": 1},
                {"run_id": runs[2], "followup_input_id": input_id},
                {"run_id": runs[2], "status": "unknown"},
            ):
                with pytest.raises(IntegrityError):
                    connection.execute(insert_attempt.values(**duplicate))
            connection.execute(
                insert_attempt.values(run_id=runs[3], goal_version=2, followup_ordinal=1)
            )

            intent = sa.insert(GoalTransitionIntent).values(
                goal_id=goal, goal_version=1, goal_revision=1, kind="completed", payload={}
            )
            connection.execute(intent.values(run_id=runs[0]))
            with pytest.raises(IntegrityError):
                connection.execute(intent.values(run_id=runs[1]))
            connection.execute(intent.values(run_id=runs[0], goal_revision=2, kind="blocked"))

            migration.downgrade()
            assert set(sa.inspect(connection).get_table_names()) == {
                "conversations",
                "conversation_runs",
                "conversation_run_inputs",
                "users",
                "tenants",
                "organizations",
            }
            assert (
                connection.exec_driver_sql("SELECT id FROM conversations").scalar_one()
                == conversation.hex
            )
    engine.dispose()
