"""``i31_workflow_chat_tools``: chain, the link table and run columns match the models."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from alembic import op as alembic_op
from app.models.agent_workflow import AgentWorkflowLink

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
PRIOR = (
    "CREATE TABLE users (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE agents (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE workflows (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE conversations (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE conversation_runs (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE workflow_runs (id CHAR(32) PRIMARY KEY, status TEXT)",
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "i31_test", VERSIONS / "i31_workflow_chat_tools.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chain() -> None:
    module = _load()
    assert (module.revision, module.down_revision) == (
        "i31_workflow_chat_tools",
        "i30_workflow_step_rounds",
    )


def test_up_and_down(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite://")
    module = _load()
    with engine.begin() as connection:
        for statement in PRIOR:
            connection.exec_driver_sql(statement)
        monkeypatch.setattr(
            alembic_op, "_proxy", Operations(MigrationContext.configure(connection)), raising=False
        )
        module.upgrade()
        inspector = inspect(connection)
        columns = {c["name"]: c["nullable"] for c in inspector.get_columns("agent_workflows")}
        assert columns == {c.name: c.nullable for c in AgentWorkflowLink.__table__.columns}
        run_columns = {c["name"] for c in inspector.get_columns("workflow_runs")}
        assert {
            "parent_conversation_id",
            "parent_tool_call_id",
            "parent_conversation_run_id",
            "chat_resumed_at",
        } <= run_columns
        uniques = {u["name"] for u in inspector.get_unique_constraints("workflow_runs")}
        assert "uq_workflow_runs_parent_tool_call" in uniques
        module.downgrade()
        inspector = inspect(connection)
        assert "agent_workflows" not in inspector.get_table_names()
        assert "parent_tool_call_id" not in {
            c["name"] for c in inspector.get_columns("workflow_runs")
        }
