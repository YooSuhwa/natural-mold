"""``i27_workflows``: chain, tables matching the models, reversibility."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, inspect

from alembic import op as alembic_op
from app.models.workflow import Workflow, WorkflowVersion

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
PRIOR = (
    "CREATE TABLE users (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE tenants (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE organizations (id CHAR(32) NOT NULL, tenant_id CHAR(32) NOT NULL, "
    "PRIMARY KEY (id), UNIQUE (id, tenant_id))",
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("i27_test", VERSIONS / "i27_workflows.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def lane(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Connection, ModuleType]]:
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        for statement in PRIOR:
            connection.exec_driver_sql(statement)
        monkeypatch.setattr(
            alembic_op, "_proxy", Operations(MigrationContext.configure(connection)), raising=False
        )
        yield connection, _load()


def test_chain() -> None:
    module = _load()
    assert (module.revision, module.down_revision) == ("i27_workflows", "i26_workflow_run_source")


def test_tables_match_the_models_and_downgrade_drops_them(lane) -> None:
    connection, module = lane
    module.upgrade()
    inspector = inspect(connection)
    for model in (Workflow, WorkflowVersion):
        columns = {c["name"]: c["nullable"] for c in inspector.get_columns(model.__tablename__)}
        assert columns == {c.name: c.nullable for c in model.__table__.columns}
    uniques = {u["name"] for u in inspector.get_unique_constraints("workflow_versions")}
    assert "uq_workflow_versions_version_no" in uniques
    module.downgrade()
    assert not {"workflows", "workflow_versions"} & set(inspect(connection).get_table_names())
