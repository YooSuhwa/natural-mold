"""``i26_workflow_run_source``: full-list MCP delegations gain WORKFLOW, reversibly."""

from __future__ import annotations

import importlib.util
import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine

from alembic import op as alembic_op
from app.authz.tuple_types import RUN_SOURCES

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"{name}_test", VERSIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lane(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Connection, ModuleType]]:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE resource_grants (id CHAR(32) PRIMARY KEY, condition_name VARCHAR(80), "
            "condition_context JSON)"
        )
        monkeypatch.setattr(
            alembic_op, "_proxy", Operations(MigrationContext.configure(connection)), raising=False
        )
        yield connection, _load("i26_workflow_run_source")


def _insert(connection: Connection, name: str | None, sources: list[str] | None) -> str:
    row_id = uuid.uuid4().hex
    context = None if sources is None else json.dumps({"allowed_sources": sources})
    connection.exec_driver_sql(
        "INSERT INTO resource_grants VALUES (?, ?, ?)", (row_id, name, context)
    )
    return row_id


def _sources(connection: Connection, row_id: str) -> list[str] | None:
    raw = connection.exec_driver_sql(
        "SELECT condition_context FROM resource_grants WHERE id = ?", (row_id,)
    ).scalar_one()
    return None if raw is None else json.loads(raw)["allowed_sources"]


def test_chain_and_lists_match_the_code() -> None:
    module = _load("i26_workflow_run_source")
    assert (module.revision, module.down_revision) == (
        "i26_workflow_run_source",
        "i25_memory_trigger_memo",
    )
    assert module.BEFORE == _load("f01_connection_delegation").RUN_SOURCES
    assert tuple(module.AFTER) == RUN_SOURCES


def test_only_full_lists_widen_and_downgrade_narrows_them_back(lane) -> None:
    connection, module = lane
    full = _insert(connection, "allowed_run_source", list(module.BEFORE))
    narrowed = _insert(connection, "allowed_run_source", ["CHAT"])
    other = _insert(connection, None, None)
    module.upgrade()
    assert _sources(connection, full) == list(module.AFTER)
    assert _sources(connection, narrowed) == ["CHAT"]
    assert _sources(connection, other) is None
    module.upgrade()  # rerun adds nothing
    assert _sources(connection, full) == list(module.AFTER)
    module.downgrade()
    assert _sources(connection, full) == list(module.BEFORE)
    assert _sources(connection, narrowed) == ["CHAT"]
