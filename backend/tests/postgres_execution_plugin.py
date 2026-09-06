"""Structured execution receipt for the canonical PostgreSQL lane."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

import pytest

_selected: list[str] = []
_deselected: list[str] = []
_executed: list[str] = []
_failed: list[str] = []
_skipped: list[str] = []


@runtime_checkable
class _CheckedOutPool(Protocol):
    def checkedout(self) -> int: ...


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    _selected[:] = sorted(item.nodeid for item in items)


def pytest_deselected(items: list[pytest.Item]) -> None:
    _deselected.extend(
        item.nodeid for item in items if item.get_closest_marker("integration") is not None
    )


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when != "call":
        if report.skipped:
            _skipped.append(report.nodeid)
        return
    _executed.append(report.nodeid)
    if report.failed:
        _failed.append(report.nodeid)
    if report.skipped:
        _skipped.append(report.nodeid)


def pytest_sessionfinish() -> None:
    destination_raw = os.environ.get("MOLDY_PG_TEST_RECEIPT")
    if not destination_raw:
        return
    destination = Path(destination_raw)
    node_bytes = "\n".join(_selected).encode()
    from app import database
    from app.agent_runtime import checkpointer
    from app.services.conversation_run_worker import get_run_task_registry
    from app.services.skill_evaluation_worker import skill_evaluation_worker

    pool = database.engine.pool
    checked_out = pool.checkedout() if isinstance(pool, _CheckedOutPool) else -1
    resource_receipt = {
        "database_checked_out": checked_out,
        "checkpointer_pool_published": checkpointer._pool is not None,
        "checkpointer_published": checkpointer._checkpointer is not None,
        "conversation_tasks_active": len(get_run_task_registry().active_run_ids()),
        "skill_worker_task_active": skill_evaluation_worker._task is not None,
    }
    payload = {
        "schema_version": 1,
        "selected_node_ids": _selected,
        "executed_node_ids": sorted(_executed),
        "deselected_node_ids": sorted(set(_deselected)),
        "failed_node_ids": sorted(set(_failed)),
        "skipped_node_ids": sorted(set(_skipped)),
        "selected_sha256": hashlib.sha256(node_bytes).hexdigest(),
        "resource_receipt": resource_receipt,
    }
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n")
    temporary.replace(destination)
