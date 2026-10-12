from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Table, UniqueConstraint

import app.models  # noqa: F401
from app.database import Base


def _table(name: str) -> Table:
    return Base.metadata.tables[name]


def _foreign_key(table: str, column: str) -> ForeignKeyConstraint:
    constraints = (
        constraint
        for constraint in _table(table).foreign_key_constraints
        if tuple(constraint.column_keys) == (column,)
    )
    return next(constraints)


def _check_names(table: str) -> set[str | None]:
    return {
        str(constraint.name) if constraint.name is not None else None
        for constraint in _table(table).constraints
        if isinstance(constraint, CheckConstraint)
    }


def _unique_columns(table: str) -> set[tuple[str, ...]]:
    return {
        tuple(constraint.columns.keys())
        for constraint in _table(table).constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_revision_follows_m83_without_consuming_m84() -> None:
    migration_path = Path("alembic/versions/b08_agent_lifecycle.py")
    spec = importlib.util.spec_from_file_location("b08_agent_lifecycle", migration_path)
    assert spec is not None
    assert spec.loader is not None
    migration = ModuleType(spec.name)
    spec.loader.exec_module(migration)

    assert migration.revision == "b08_agent_lifecycle"
    assert migration.down_revision == "m83_org_scope_not_null"


def test_retained_graph_tables_are_registered() -> None:
    expected = {
        "agent_purge_cleanup_items",
        "agent_purge_receipts",
        "conversation_history_checkpoints",
        "conversation_history_events",
        "conversation_history_manifests",
        "conversation_run_agents",
    }

    assert expected <= set(Base.metadata.tables)


def test_retained_agent_references_are_nullable_set_null() -> None:
    retained = {
        "conversation_artifacts": "agent_id",
        "conversation_run_inputs": "agent_id",
        "conversation_runs": "agent_id",
        "mcp_app_invocation_bindings": "agent_id",
        "token_usages": "agent_id",
    }

    for table_name, column_name in retained.items():
        table = _table(table_name)
        foreign_key = _foreign_key(table_name, column_name)
        assert table.c[column_name].nullable is True
        assert foreign_key.ondelete == "SET NULL"


def test_conversation_owned_descendants_keep_cascade() -> None:
    descendants = {
        "conversation_artifacts": "conversation_id",
        "conversation_history_checkpoints": "conversation_id",
        "conversation_history_events": "conversation_id",
        "conversation_history_manifests": "conversation_id",
        "conversation_run_inputs": "conversation_id",
        "conversation_runs": "conversation_id",
        "mcp_app_invocation_bindings": "conversation_id",
        "token_usages": "conversation_id",
    }

    for table_name, column_name in descendants.items():
        assert _foreign_key(table_name, column_name).ondelete == "CASCADE"


def test_conversation_lifecycle_columns_and_checks_are_declared() -> None:
    table = _table("conversations")

    assert table.c.agent_lifecycle_state.nullable is False
    assert table.c.agent_lifecycle_state.server_default is not None
    assert table.c.agent_archived_at_snapshot.nullable is True
    assert table.c.agent_purged_at.nullable is True
    assert {
        "ck_conversations_agent_lifecycle_state",
        "ck_conversations_agent_lifecycle_binding",
    } <= _check_names("conversations")


def test_run_participant_contract_is_declared() -> None:
    table = _table("conversation_run_agents")

    assert table.c.agent_id.nullable is True
    assert _foreign_key("conversation_run_agents", "agent_id").ondelete == "SET NULL"
    assert _foreign_key("conversation_run_agents", "run_id").ondelete == "CASCADE"
    assert ("run_id", "agent_id_snapshot") in _unique_columns("conversation_run_agents")
    assert "ck_conversation_run_agents_role" in _check_names("conversation_run_agents")


def test_api_and_trigger_envelopes_preserve_snapshots_and_run_links() -> None:
    for table_name in ("agent_api_threads", "agent_api_runs"):
        table = _table(table_name)
        assert table.c.deployment_id.nullable is True
        assert table.c.deployment_public_id_snapshot.nullable is False
        assert _foreign_key(table_name, "deployment_id").ondelete == "SET NULL"

    api_runs = _table("agent_api_runs")
    trigger_runs = _table("agent_trigger_runs")
    assert api_runs.c.conversation_run_id.nullable is True
    assert trigger_runs.c.conversation_run_id.nullable is True
    assert ("conversation_run_id",) in _unique_columns("agent_api_runs")
    assert ("conversation_run_id",) in _unique_columns("agent_trigger_runs")
    assert trigger_runs.c.trigger_name_snapshot.nullable is False
    assert trigger_runs.c.trigger_id.nullable is True
    assert trigger_runs.c.agent_id.nullable is True
    assert _foreign_key("agent_trigger_runs", "trigger_id").ondelete == "SET NULL"
    assert _foreign_key("agent_trigger_runs", "agent_id").ondelete == "SET NULL"


def test_history_and_cleanup_state_constraints_are_declared() -> None:
    expected_checks = {
        "conversation_history_manifests": {
            "ck_conversation_history_manifests_state",
            "ck_conversation_history_manifests_counts",
        },
        "conversation_history_checkpoints": {
            "ck_conversation_history_checkpoints_checksum",
        },
        "conversation_history_events": {
            "ck_conversation_history_events_sequence",
            "ck_conversation_history_events_checksum",
        },
        "agent_purge_receipts": {"ck_agent_purge_receipts_state"},
        "agent_purge_cleanup_items": {
            "ck_agent_purge_cleanup_items_kind",
            "ck_agent_purge_cleanup_items_status",
            "ck_agent_purge_cleanup_items_attempts",
        },
    }

    for table_name, constraint_names in expected_checks.items():
        assert constraint_names <= _check_names(table_name)

    assert ("conversation_id", "schema_version") in _unique_columns(
        "conversation_history_manifests"
    )
    assert ("tenant_id", "org_id", "agent_id_snapshot") in _unique_columns("agent_purge_receipts")
    assert ("receipt_id", "kind", "locator") in _unique_columns("agent_purge_cleanup_items")
