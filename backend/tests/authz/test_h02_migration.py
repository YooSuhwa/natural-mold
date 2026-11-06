"""H-02 ``h02_notifications`` (plan m88 row): table, kind check, reversibility."""

import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, inspect
from sqlalchemy.exc import IntegrityError

from alembic import op as alembic_op
from app.models.notification import NOTIFICATION_KINDS

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
PRIOR = (
    "CREATE TABLE users (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE tenants (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE organizations (id CHAR(32) NOT NULL, tenant_id CHAR(32) NOT NULL, "
    "PRIMARY KEY (id), UNIQUE (id, tenant_id))",
)
ORG, TENANT, USER = (uuid.uuid4().hex for _ in range(3))


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("h02_test", VERSIONS / "h02_notifications.py")
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
        connection.exec_driver_sql("INSERT INTO tenants VALUES (?)", (TENANT,))
        connection.exec_driver_sql("INSERT INTO organizations VALUES (?, ?)", (ORG, TENANT))
        monkeypatch.setattr(
            alembic_op, "_proxy", Operations(MigrationContext.configure(connection)), raising=False
        )
        yield connection, _load()


def _insert(connection: Connection, kind: str, org: str | None = ORG) -> None:
    connection.exec_driver_sql(
        "INSERT INTO notifications (id, org_id, tenant_id, user_id, kind, payload) "
        "VALUES (?, ?, ?, ?, ?, '{}')",
        (uuid.uuid4().hex, org, TENANT, USER, kind),
    )


def test_revision_chain_and_kinds_match_the_model() -> None:
    module = _load()
    assert (module.revision, module.down_revision) == (
        "h02_notifications",
        "h03_audit_tenant_index",
    )
    # Later migrations add kinds on top of h02's list (i16: mcp_tool.review_needed,
    # i17: the approval inbox kinds,
    # i19: the emergency stop kinds).
    i16, i17 = _later("i16_mcp_tool_review"), _later("i17_approval_requests")
    i19 = _later("i19_emergency_stops")
    assert i16._KINDS_BEFORE == module._KINDS
    assert (*module._KINDS, i16._NEW_KIND) == i17._KINDS_BEFORE
    assert (*i17._KINDS_BEFORE, *i17._NEW_KINDS) == i19._KINDS_BEFORE
    assert (*i19._KINDS_BEFORE, *i19._NEW_KINDS) == NOTIFICATION_KINDS


def _later(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"{name}_test", VERSIONS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_kinds_are_checked_and_company_rows_have_no_org(lane) -> None:
    connection, module = lane
    module.upgrade()
    _insert(connection, "trigger.blocked")
    _insert(connection, "tenant.suspended", org=None)
    with pytest.raises(IntegrityError):
        _insert(connection, "unknown.kind")
    indexes = {ix["name"] for ix in inspect(connection).get_indexes("notifications")}
    assert "ix_notifications_user_read_created" in indexes


def test_downgrade_drops_the_table(lane) -> None:
    connection, module = lane
    module.upgrade()
    module.downgrade()
    assert "notifications" not in inspect(connection).get_table_names()
