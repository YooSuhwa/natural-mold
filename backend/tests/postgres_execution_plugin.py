"""Structured execution receipt for the canonical PostgreSQL lane."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Literal, Protocol, TypedDict, runtime_checkable

import pytest

type ProgressPhase = Literal[
    "collection", "start", "setup", "call", "teardown", "finish", "session_finish"
]


class _ProgressState(TypedDict):
    schema_version: int
    sequence: int
    state: Literal["running", "finished"]
    session_elapsed_ns: int
    active_node_id: str | None
    active_phase: ProgressPhase
    completed_node_ids: list[str]
    node_elapsed_ns: dict[str, int]


class _PostgresTestReceipt(TypedDict):
    schema_version: int
    selected_node_ids: list[str]
    executed_node_ids: list[str]
    deselected_node_ids: list[str]
    failed_node_ids: list[str]
    skipped_node_ids: list[str]
    selected_sha256: str
    resource_receipt: dict[str, bool | int] | None
    progress: _ProgressState


_selected: list[str] = []
_deselected: list[str] = []
_executed: list[str] = []
_failed: list[str] = []
_skipped: list[str] = []
_completed: list[str] = []
_node_started_ns: dict[str, int] = {}
_node_elapsed_ns: dict[str, int] = {}
_sequence = 0
_session_started_ns = time.monotonic_ns()
_active_node_id: str | None = None


@runtime_checkable
class _CheckedOutPool(Protocol):
    def checkedout(self) -> int: ...


def _destination() -> Path | None:
    raw = os.environ.get("MOLDY_PG_TEST_RECEIPT")
    return Path(raw) if raw else None


def _atomic_publish(destination: Path, payload: _PostgresTestReceipt) -> None:
    descriptor, temporary_raw = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_raw)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
        directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _payload(
    state: Literal["running", "finished"],
    phase: ProgressPhase,
    resource_receipt: dict[str, bool | int] | None,
) -> _PostgresTestReceipt:
    node_bytes = "\n".join(_selected).encode()
    return {
        "schema_version": 1,
        "selected_node_ids": list(_selected),
        "executed_node_ids": sorted(set(_executed)),
        "deselected_node_ids": sorted(set(_deselected)),
        "failed_node_ids": sorted(set(_failed)),
        "skipped_node_ids": sorted(set(_skipped)),
        "selected_sha256": hashlib.sha256(node_bytes).hexdigest(),
        "resource_receipt": resource_receipt,
        "progress": {
            "schema_version": 1,
            "sequence": _sequence,
            "state": state,
            "session_elapsed_ns": max(0, time.monotonic_ns() - _session_started_ns),
            "active_node_id": _active_node_id,
            "active_phase": phase,
            "completed_node_ids": list(_completed),
            "node_elapsed_ns": dict(_node_elapsed_ns),
        },
    }


def _checkpoint(
    phase: ProgressPhase,
    state: Literal["running", "finished"] = "running",
    resource_receipt: dict[str, bool | int] | None = None,
) -> None:
    global _sequence
    destination = _destination()
    if destination is None:
        return
    _sequence += 1
    _atomic_publish(destination, _payload(state, phase, resource_receipt))


def pytest_sessionstart() -> None:
    global _active_node_id, _sequence, _session_started_ns
    _selected.clear()
    _deselected.clear()
    _executed.clear()
    _failed.clear()
    _skipped.clear()
    _completed.clear()
    _node_started_ns.clear()
    _node_elapsed_ns.clear()
    _active_node_id = None
    _sequence = 0
    _session_started_ns = time.monotonic_ns()


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    _selected[:] = sorted(item.nodeid for item in items)
    _checkpoint("collection")


def pytest_deselected(items: list[pytest.Item]) -> None:
    _deselected.extend(
        item.nodeid for item in items if item.get_closest_marker("integration") is not None
    )


def pytest_runtest_logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
    global _active_node_id
    del location
    _active_node_id = nodeid
    _node_started_ns[nodeid] = time.monotonic_ns()
    _checkpoint("start")


def _mark_phase(nodeid: str, phase: ProgressPhase) -> None:
    global _active_node_id
    _active_node_id = nodeid
    _checkpoint(phase)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    _mark_phase(item.nodeid, "setup")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item: pytest.Item) -> None:
    _mark_phase(item.nodeid, "call")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    del nextitem
    _mark_phase(item.nodeid, "teardown")


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    global _active_node_id
    _active_node_id = report.nodeid
    if report.when == "call":
        _executed.append(report.nodeid)
    if report.failed:
        _failed.append(report.nodeid)
    if report.skipped:
        _skipped.append(report.nodeid)
    phase: ProgressPhase = report.when
    _checkpoint(phase)


def pytest_runtest_logfinish(nodeid: str, location: tuple[str, int | None, str]) -> None:
    global _active_node_id
    del location
    started = _node_started_ns.pop(nodeid, time.monotonic_ns())
    _node_elapsed_ns[nodeid] = max(0, time.monotonic_ns() - started)
    if nodeid not in _completed:
        _completed.append(nodeid)
    _active_node_id = nodeid
    _checkpoint("finish")


def pytest_sessionfinish() -> None:
    global _active_node_id
    if _destination() is None:
        return
    from app import database
    from app.agent_runtime import checkpointer
    from app.services.conversation_run_worker import get_run_task_registry
    from app.services.skill_evaluation_worker import skill_evaluation_worker

    pool = database.engine.pool
    checked_out = pool.checkedout() if isinstance(pool, _CheckedOutPool) else -1
    resource_receipt: dict[str, bool | int] = {
        "database_checked_out": checked_out,
        "checkpointer_pool_published": checkpointer._pool is not None,
        "checkpointer_published": checkpointer._checkpointer is not None,
        "conversation_tasks_active": len(get_run_task_registry().active_run_ids()),
        "skill_worker_task_active": skill_evaluation_worker._task is not None,
    }
    _active_node_id = None
    _checkpoint("session_finish", "finished", resource_receipt)
