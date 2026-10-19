"""F-04 m87 ``f04_credential_modes``: backfill and reversibility on a minimal schema."""

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, inspect

from alembic import op as alembic_op

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
PRIOR = (
    "CREATE TABLE users (id CHAR(32) PRIMARY KEY)",
    "CREATE TABLE organizations (id CHAR(32), tenant_id CHAR(32), PRIMARY KEY (id, tenant_id))",
    "CREATE TABLE credentials (id CHAR(32) PRIMARY KEY, definition_key VARCHAR(80))",
    "CREATE TABLE tools (id CHAR(32) PRIMARY KEY, credential_id CHAR(32))",
    "CREATE TABLE mcp_servers (id CHAR(32) PRIMARY KEY, url VARCHAR(500), credential_id CHAR(32))",
    "INSERT INTO credentials VALUES ('c1', 'mcp_oauth2'), ('c2', 'http_bearer')",
    "INSERT INTO tools VALUES ('t1', 'c2'), ('t2', NULL)",
    "INSERT INTO mcp_servers VALUES ('s1', 'https://MCP.Example.com:8443/mcp', 'c1'), "
    "('s2', 'https://two.example.com', 'c1'), ('s3', NULL, NULL)",
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("f04_test", VERSIONS / "f04_credential_modes.py")
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


def _all(connection: Connection, sql: str) -> list[tuple]:
    return [tuple(row) for row in connection.exec_driver_sql(sql)]


def test_revision_chain() -> None:
    module = _load()
    assert (module.revision, module.down_revision) == (
        "f04_credential_modes",
        "d02_agent_user_preferences",
    )


def test_upgrade_backfills_modes_types_and_hosts(lane: tuple[Connection, ModuleType]) -> None:
    connection, migration = lane
    migration.upgrade()
    assert _all(
        connection, "SELECT id, credential_mode, credential_definition_key FROM tools ORDER BY id"
    ) == [("t1", "shared", "http_bearer"), ("t2", "none", None)]
    assert _all(connection, "SELECT id, credential_mode FROM mcp_servers ORDER BY id") == [
        ("s1", "shared"),
        ("s2", "shared"),
        ("s3", "none"),
    ]
    assert _all(connection, "SELECT id, allowed_hosts FROM credentials ORDER BY id") == [
        ("c1", '["mcp.example.com", "two.example.com"]'),
        ("c2", None),
    ]
    assert "resource_credential_bindings" in inspect(connection).get_table_names()
    connection.exec_driver_sql("INSERT INTO tools (id, credential_id) VALUES ('t3', NULL)")
    assert _all(connection, "SELECT credential_mode FROM tools WHERE id = 't3'") == [("none",)]


def test_downgrade_restores_the_prior_schema(lane: tuple[Connection, ModuleType]) -> None:
    connection, migration = lane
    migration.upgrade()
    migration.downgrade()
    columns = {c["name"] for c in inspect(connection).get_columns("tools")}
    assert columns == {"id", "credential_id"}
    assert "allowed_hosts" not in {
        c["name"] for c in inspect(connection).get_columns("credentials")
    }
    assert "resource_credential_bindings" not in inspect(connection).get_table_names()
