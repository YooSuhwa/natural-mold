"""F-14 ``f14_run_attribution`` (plan m89 row): owner backfill and reversibility."""

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

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_SPEND = (
    "id CHAR(1) PRIMARY KEY, date DATE, {axis}_id CHAR(2), total_tokens_in INTEGER, "
    "total_tokens_out INTEGER, total_cost_usd NUMERIC, request_count INTEGER, "
    "updated_at DATETIME, CONSTRAINT uq_daily_spend_{axis}"
)
PRIOR = (
    "CREATE TABLE users (id CHAR(2) PRIMARY KEY)",
    "CREATE TABLE tenants (id CHAR(2) PRIMARY KEY)",
    "CREATE TABLE organizations (id CHAR(2), tenant_id CHAR(2), is_default BOOLEAN, "
    "PRIMARY KEY (id, tenant_id))",
    "CREATE TABLE tenant_members (tenant_id CHAR(2), user_id CHAR(2))",
    "CREATE TABLE models (id CHAR(2) PRIMARY KEY, tenant_id CHAR(2))",
    "CREATE TABLE agents (id CHAR(2) PRIMARY KEY, user_id CHAR(2), org_id CHAR(2), "
    "tenant_id CHAR(2))",
    "CREATE TABLE conversations (id CHAR(2) PRIMARY KEY, user_id CHAR(2), org_id CHAR(2), "
    "tenant_id CHAR(2))",
    "CREATE TABLE conversation_runs (id CHAR(2) PRIMARY KEY, conversation_id CHAR(2), "
    "agent_id CHAR(2), user_id CHAR(2), created_at DATETIME)",
    "CREATE TABLE agent_api_runs (id CHAR(2) PRIMARY KEY, conversation_run_id CHAR(2))",
    "CREATE TABLE agent_trigger_runs (id CHAR(2) PRIMARY KEY, conversation_run_id CHAR(2))",
    "CREATE TABLE token_usages (id CHAR(2) PRIMARY KEY, run_id CHAR(2), "
    "conversation_id CHAR(2), agent_id CHAR(2), created_at DATETIME)",
    "CREATE TABLE conversation_run_agents (id CHAR(2) PRIMARY KEY, run_id CHAR(2), "
    "agent_id_snapshot CHAR(2), role VARCHAR(20))",
    "CREATE TABLE audit_events (id CHAR(2) PRIMARY KEY, tenant_id CHAR(2), created_at DATETIME)",
    *(
        f"CREATE TABLE daily_spend_{axis} ({_SPEND.format(axis=axis)}_date_{axis} "
        f"UNIQUE (date, {axis}_id))"
        for axis in ("user", "agent", "model")
    ),
    "INSERT INTO organizations VALUES ('o1', 't1', 1), ('o2', 't1', 0)",
    "INSERT INTO tenant_members VALUES ('t1', 'u1')",
    "INSERT INTO models VALUES ('m1', 't1')",
    "INSERT INTO agents VALUES ('a1', 'u1', 'o2', 't1'), ('a2', 'u2', 'o2', 't1')",
    "INSERT INTO conversations VALUES ('c1', 'u1', 'o2', 't1')",
    "INSERT INTO conversation_runs VALUES ('r1', 'c1', 'a1', 'u1', NULL), "
    "('r2', 'c1', 'a1', 'u1', NULL), ('r3', 'c1', 'a1', 'u1', NULL)",
    "INSERT INTO agent_api_runs VALUES ('x1', 'r2')",
    "INSERT INTO agent_trigger_runs VALUES ('y1', 'r3')",
    "INSERT INTO token_usages VALUES ('k1', 'r2', 'c1', 'a1', NULL), "
    "('k2', NULL, 'c1', 'a1', NULL)",
    "INSERT INTO conversation_run_agents VALUES ('p1', 'r1', 'a1', 'root'), "
    "('p2', 'r1', 'a2', 'subagent')",
    "INSERT INTO daily_spend_user VALUES ('s', '2026-10-01', 'u1', 1, 1, 1, 1, NULL)",
    "INSERT INTO daily_spend_agent VALUES ('s', '2026-10-01', 'a1', 1, 1, 1, 1, NULL)",
    "INSERT INTO daily_spend_model VALUES ('s', '2026-10-01', 'm1', 1, 1, 1, 1, NULL)",
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("f14_test", VERSIONS / "f14_run_attribution.py")
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
        "f14_run_attribution",
        "f09_agent_memo",
    )


def test_upgrade_backfills_owner_attribution_and_org_spend(
    lane: tuple[Connection, ModuleType],
) -> None:
    connection, migration = lane
    migration.upgrade()
    assert _all(
        connection,
        "SELECT id, org_id, tenant_id, run_source, invoker_type, resource_owner_user_id, "
        "on_behalf_of_user_id, credential_subject_user_id FROM conversation_runs ORDER BY id",
    ) == [
        ("r1", "o2", "t1", "CHAT", "user", "u1", "u1", "u1"),
        ("r2", "o2", "t1", "API", "api", "u1", "u1", "u1"),
        ("r3", "o2", "t1", "TRIGGER", "schedule", "u1", "u1", "u1"),
    ]
    assert _all(
        connection,
        "SELECT id, org_id, caller_user_id, run_source, resource_owner_user_id, "
        "credential_subject_user_id FROM token_usages ORDER BY id",
    ) == [("k1", "o2", "u1", "API", "u1", "u1"), ("k2", "o2", "u1", None, "u1", "u1")]
    assert _all(
        connection,
        "SELECT id, invoker_type, resource_owner_user_id, on_behalf_of_user_id "
        "FROM conversation_run_agents ORDER BY id",
    ) == [("p1", "user", "u1", "u1"), ("p2", "subagent", "u2", "u2")]
    for axis, org in (("user", "o1"), ("agent", "o2"), ("model", "o1")):
        query = f"SELECT org_id, tenant_id FROM daily_spend_{axis}"  # noqa: S608
        assert _all(connection, query) == [(org, "t1")]
    # The UPSERT key is now organization-scoped: the same day/user in another org is a row.
    connection.exec_driver_sql(
        "INSERT INTO daily_spend_user VALUES "
        "('t', '2026-10-01', 'u1', 2, 3, 4, 5, NULL, 'o2', 't1')"
    )
    assert "org_id" in {c["name"] for c in inspect(connection).get_columns("audit_events")}
    indexes = {i["name"] for i in inspect(connection).get_indexes("token_usages")}
    assert indexes == {"ix_token_usages_org_created", "ix_token_usages_agent_caller"}


def test_downgrade_restores_the_prior_schema(lane: tuple[Connection, ModuleType]) -> None:
    connection, migration = lane
    migration.upgrade()
    connection.exec_driver_sql(
        "INSERT INTO daily_spend_user VALUES "
        "('t', '2026-10-01', 'u1', 2, 3, 4, 5, NULL, 'o2', 't1')"
    )
    migration.downgrade()
    # Per-organization rows for the same day/user fold into one row with summed totals.
    assert _all(
        connection,
        "SELECT id, total_tokens_in, total_tokens_out, total_cost_usd, request_count "
        "FROM daily_spend_user",
    ) == [("s", 3, 4, 5, 6)]
    inspector = inspect(connection)
    assert {c["name"] for c in inspector.get_columns("conversation_runs")} == {
        "id",
        "conversation_id",
        "agent_id",
        "user_id",
        "created_at",
    }
    assert inspector.get_indexes("token_usages") == []
    assert {c["name"] for c in inspector.get_columns("audit_events")} == {
        "id",
        "tenant_id",
        "created_at",
    }
    assert [u["column_names"] for u in inspector.get_unique_constraints("daily_spend_user")] == [
        ["date", "user_id"]
    ]
