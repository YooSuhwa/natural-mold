from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "alembic" / "versions" / "m74_conversation_run_metrics.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("m74_metrics_test", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_m74_is_linear_and_creates_run_association_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    for operation in (
        "create_table",
        "add_column",
        "create_foreign_key",
        "create_unique_constraint",
    ):
        monkeypatch.setattr(
            migration.op,
            operation,
            lambda *args, _operation=operation, **kwargs: calls.append((_operation, args, kwargs)),
        )

    migration.upgrade()

    assert migration.revision == "m74_conversation_run_metrics"
    assert migration.down_revision == "m73_conversation_run_inputs"
    assert calls[0][0] == "create_table"
    assert calls[0][1][0] == "conversation_run_metrics"
    assert {column.name for column in calls[0][1][1:]} >= {
        "run_id",
        "terminal_state",
        "usage_complete",
        "activity_json",
    }
    assert calls[1][0] == "add_column"
    assert calls[1][1][0] == "token_usages"
    assert calls[1][1][1].name == "run_id"
    assert calls[2] == (
        "create_foreign_key",
        (
            "fk_token_usages_run_id",
            "token_usages",
            "conversation_runs",
            ["run_id"],
            ["id"],
        ),
        {"ondelete": "CASCADE"},
    )
    assert calls[3] == (
        "create_unique_constraint",
        ("uq_token_usages_run_id", "token_usages", ["run_id"]),
        {},
    )
