"""``i30_workflow_step_rounds``: chain, existing rows become round 1, reversibility."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from alembic import op as alembic_op

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
TABLE = (
    "CREATE TABLE workflow_step_runs (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, "
    "node_id TEXT NOT NULL, conversation_run_id TEXT, "
    "CONSTRAINT uq_workflow_step_runs_node UNIQUE (run_id, node_id), "
    "CONSTRAINT uq_workflow_step_runs_conversation_run UNIQUE (conversation_run_id))"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "i30_test", VERSIONS / "i30_workflow_step_rounds.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chain() -> None:
    module = _load()
    assert (module.revision, module.down_revision) == (
        "i30_workflow_step_rounds",
        "i29_workflow_approvals",
    )


def test_rounds_up_and_down(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite://")
    module = _load()
    with engine.begin() as connection:
        connection.exec_driver_sql(TABLE)
        connection.exec_driver_sql(
            "INSERT INTO workflow_step_runs (id, run_id, node_id) VALUES (1, 'r', 'draft')"
        )
        monkeypatch.setattr(
            alembic_op, "_proxy", Operations(MigrationContext.configure(connection)), raising=False
        )
        module.upgrade()
        # The existing row is round 1; a second round of the same node now fits.
        assert connection.exec_driver_sql("SELECT round FROM workflow_step_runs").scalar() == 1
        connection.exec_driver_sql(
            "INSERT INTO workflow_step_runs (id, run_id, node_id, round) "
            "VALUES (2, 'r', 'draft', 2)"
        )
        uniques = {
            u["name"] for u in inspect(connection).get_unique_constraints("workflow_step_runs")
        }
        assert "uq_workflow_step_runs_node_round" in uniques
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.exec_driver_sql(
                "INSERT INTO workflow_step_runs (id, run_id, node_id, round) "
                "VALUES (3, 'r', 'draft', 2)"
            )

        module.downgrade()
        # Later rounds are dropped so the one-row-per-node key can come back.
        assert connection.exec_driver_sql("SELECT id FROM workflow_step_runs").scalars().all() == [
            1
        ]
        columns = {c["name"] for c in inspect(connection).get_columns("workflow_step_runs")}
        assert "round" not in columns
