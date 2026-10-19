"""H-01 ``h01_access_requests`` (plan m88 row): table, pending uniqueness, reversibility."""

import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, inspect
from sqlalchemy.exc import IntegrityError

from alembic import op as alembic_op

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
PRIOR = (
    "CREATE TABLE users (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE organizations (id CHAR(32) NOT NULL, tenant_id CHAR(32) NOT NULL, "
    "PRIMARY KEY (id), UNIQUE (id, tenant_id))",
)
ORG, TENANT, USER = (uuid.uuid4().hex for _ in range(3))


def _load() -> ModuleType:
    path = VERSIONS / "h01_access_requests.py"
    spec = importlib.util.spec_from_file_location("h01_test", path)
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
        connection.exec_driver_sql("INSERT INTO users VALUES (?)", (USER,))
        connection.exec_driver_sql("INSERT INTO organizations VALUES (?, ?)", (ORG, TENANT))
        monkeypatch.setattr(
            alembic_op, "_proxy", Operations(MigrationContext.configure(connection)), raising=False
        )
        yield connection, _load()


def _insert(connection: Connection, status: str) -> None:
    connection.exec_driver_sql(
        "INSERT INTO access_requests (id, org_id, tenant_id, resource_type, resource_id, "
        "relation, requester_id, status, expires_at) VALUES (?, ?, ?, 'agent', 'r1', "
        "'editor', ?, ?, '2026-12-01')",
        (uuid.uuid4().hex, ORG, TENANT, USER, status),
    )


def test_revision_chain() -> None:
    module = _load()
    assert (module.revision, module.down_revision) == (
        "h01_access_requests",
        "f08_trigger_run_reason_code",
    )


def test_one_pending_request_per_resource_requester_and_level(lane) -> None:
    connection, module = lane
    module.upgrade()
    _insert(connection, "pending")
    _insert(connection, "denied")  # closed rows do not count
    with pytest.raises(IntegrityError):
        _insert(connection, "pending")
    with pytest.raises(IntegrityError):
        _insert(connection, "unknown")


def test_downgrade_drops_the_table(lane) -> None:
    connection, module = lane
    module.upgrade()
    module.downgrade()
    assert "access_requests" not in inspect(connection).get_table_names()
