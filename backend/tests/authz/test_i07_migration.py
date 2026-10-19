"""I-07 ``i07_tenant_exports``: ORM parity, one active export per company, reversibility."""

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
from app.models.tenant_export import TenantExport

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
PRIOR = (
    "CREATE TABLE tenants (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE users (id CHAR(32) PRIMARY KEY)",
    *(
        f"CREATE TABLE {name} (id CHAR(32) PRIMARY KEY, tenant_id CHAR(32))"
        for name in ("agent_triggers", "agents", "conversations", "agent_deployments")
    ),
    "CREATE TABLE agent_trigger_runs (id CHAR(32) PRIMARY KEY, trigger_id CHAR(32), "
    "agent_id CHAR(32), conversation_id CHAR(32))",
    "CREATE TABLE agent_api_runs (id CHAR(32) PRIMARY KEY, deployment_id CHAR(32), "
    "conversation_id CHAR(32))",
    "CREATE TABLE marketplace_installations (id CHAR(32) PRIMARY KEY, "
    "item_id CHAR(32) NOT NULL, version_id CHAR(32) NOT NULL, "
    "install_status VARCHAR(30) NOT NULL DEFAULT 'active', "
    "CONSTRAINT ck_marketplace_install_status CHECK (install_status IN "
    "('active','needs_setup','disabled','uninstalled')))",
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("i07_test", VERSIONS / "i07_tenant_exports.py")
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


def _job(connection: Connection, tenant: str, status: str) -> None:
    connection.exec_driver_sql(
        "INSERT INTO tenant_exports (id, tenant_id, status) VALUES (?, ?, ?)",
        (uuid.uuid4().hex, tenant, status),
    )


def test_upgrade_matches_the_model_and_allows_one_active_job(lane) -> None:
    connection, module = lane
    assert module.down_revision == "h04_authz_reconcile_runs"
    module.upgrade()
    columns = {column["name"] for column in inspect(connection).get_columns("tenant_exports")}
    assert columns == set(TenantExport.__table__.columns.keys())
    indexes = {index["name"] for index in inspect(connection).get_indexes("tenant_exports")}
    assert {"ix_tenant_exports_tenant_created", "uq_tenant_exports_active"} <= indexes
    tenant = uuid.uuid4().hex
    _job(connection, tenant, "succeeded")
    _job(connection, tenant, "failed")
    _job(connection, tenant, "queued")
    with pytest.raises(IntegrityError):
        _job(connection, tenant, "running")
    _job(connection, uuid.uuid4().hex, "running")
    with pytest.raises(IntegrityError):
        _job(connection, tenant, "expired")


def test_downgrade_drops_the_table(lane) -> None:
    connection, module = lane
    module.upgrade()
    module.downgrade()
    assert "tenant_exports" not in inspect(connection).get_table_names()


def test_runs_are_backfilled_and_installations_can_lose_their_source(lane) -> None:
    connection, module = lane
    run = connection.exec_driver_sql
    run("INSERT INTO agent_triggers VALUES ('t1', 'co1')")
    run("INSERT INTO conversations VALUES ('c1', 'co2')")
    run("INSERT INTO agent_deployments VALUES ('d1', 'co3')")
    run("INSERT INTO agent_trigger_runs VALUES ('r1', 't1', NULL, NULL)")
    run("INSERT INTO agent_trigger_runs VALUES ('r2', NULL, NULL, 'c1')")
    run("INSERT INTO agent_api_runs VALUES ('a1', 'd1', NULL)")
    module.upgrade()
    tenants = dict(run("SELECT id, tenant_id FROM agent_trigger_runs").fetchall())
    assert tenants == {"r1": "co1", "r2": "co2"}
    assert run("SELECT tenant_id FROM agent_api_runs").scalar() == "co3"
    run(
        "INSERT INTO marketplace_installations (id, item_id, version_id, install_status) "
        "VALUES ('i1', NULL, NULL, 'source_removed')"
    )
    with pytest.raises(RuntimeError):
        module.downgrade()
    run("DELETE FROM marketplace_installations")
    module.downgrade()
    with pytest.raises(IntegrityError):
        run(
            "INSERT INTO marketplace_installations (id, item_id, version_id) "
            "VALUES ('i2', NULL, 'v')"
        )
