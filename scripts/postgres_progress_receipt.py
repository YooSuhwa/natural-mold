"""Strict no-follow loader for bounded PostgreSQL pytest progress receipts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Final, Literal, TypedDict

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

MAX_RECEIPT_BYTES: Final = 2 * 1024 * 1024
MAX_NODE_COUNT: Final = 10_000
MAX_NODE_ID_CHARS: Final = 1_000
_RECEIPT_KEYS: Final = frozenset(
    {
        "schema_version",
        "selected_node_ids",
        "executed_node_ids",
        "deselected_node_ids",
        "failed_node_ids",
        "skipped_node_ids",
        "selected_sha256",
        "resource_receipt",
        "progress",
    }
)
_PROGRESS_KEYS: Final = frozenset(
    {
        "schema_version",
        "sequence",
        "state",
        "session_elapsed_ns",
        "active_node_id",
        "active_phase",
        "completed_node_ids",
        "node_elapsed_ns",
    }
)
_PHASES: Final = frozenset(
    {"collection", "start", "setup", "call", "teardown", "finish", "session_finish"}
)
_RESOURCE_KEYS: Final = frozenset(
    {
        "database_checked_out",
        "checkpointer_pool_published",
        "checkpointer_published",
        "conversation_tasks_active",
        "skill_worker_task_active",
    }
)
_RESOURCE_BOOL_KEYS: Final = frozenset(
    {"checkpointer_pool_published", "checkpointer_published", "skill_worker_task_active"}
)


class ProgressReceiptError(RuntimeError):
    """Stable failure for missing, unsafe, or malformed progress evidence."""


class ProgressState(TypedDict):
    schema_version: int
    sequence: int
    state: Literal["running", "finished"]
    session_elapsed_ns: int
    active_node_id: str | None
    active_phase: str
    completed_node_ids: list[str]
    node_elapsed_ns: dict[str, int]


class PostgresTestReceipt(TypedDict):
    schema_version: int
    selected_node_ids: list[str]
    executed_node_ids: list[str]
    deselected_node_ids: list[str]
    failed_node_ids: list[str]
    skipped_node_ids: list[str]
    selected_sha256: str
    resource_receipt: dict[str, bool | int] | None
    progress: ProgressState


def _read_json(path: Path) -> JsonValue:
    try:
        before = path.lstat()
    except FileNotFoundError as error:
        raise ProgressReceiptError("progress_receipt_missing") from error
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or before.st_mode & 0o022
        or before.st_size > MAX_RECEIPT_BYTES
        or not hasattr(os, "O_NOFOLLOW")
    ):
        raise ProgressReceiptError("unsafe_progress_receipt")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_uid != os.geteuid()
                or opened.st_nlink != 1
                or opened.st_mode & 0o022
            ):
                raise ProgressReceiptError("unsafe_progress_receipt")
            data = stream.read(MAX_RECEIPT_BYTES + 1)
        after = path.lstat()
    except OSError as error:
        raise ProgressReceiptError("unsafe_progress_receipt") from error
    if (
        len(data) > MAX_RECEIPT_BYTES
        or not stat.S_ISREG(after.st_mode)
        or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
        or after.st_uid != os.geteuid()
        or after.st_nlink != 1
        or after.st_mode & 0o022
    ):
        raise ProgressReceiptError("unsafe_progress_receipt")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProgressReceiptError("invalid_progress_receipt") from error


def _mapping(value: JsonValue, reason: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ProgressReceiptError(reason)
    return value


def _node_ids(value: JsonValue, reason: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_NODE_COUNT:
        raise ProgressReceiptError(reason)
    nodes: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or len(item) > MAX_NODE_ID_CHARS
            or any(character in item for character in ("\x00", "\n", "\r"))
        ):
            raise ProgressReceiptError(reason)
        nodes.append(item)
    if len(nodes) != len(set(nodes)):
        raise ProgressReceiptError(reason)
    return nodes


def _nonnegative_int(value: JsonValue, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProgressReceiptError(reason)
    return value


def _resource_receipt(raw: JsonValue) -> dict[str, bool | int]:
    value = _mapping(raw, "invalid_progress_receipt")
    if set(value) != _RESOURCE_KEYS:
        raise ProgressReceiptError("invalid_progress_receipt")
    for key in _RESOURCE_BOOL_KEYS:
        if not isinstance(value[key], bool):
            raise ProgressReceiptError("invalid_progress_receipt")
    for key in _RESOURCE_KEYS - _RESOURCE_BOOL_KEYS:
        item = value[key]
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= MAX_NODE_COUNT:
            raise ProgressReceiptError("invalid_progress_receipt")
    return {key: item for key, item in value.items() if isinstance(item, (bool, int))}


def _progress(
    raw: JsonValue,
    selected: list[str],
    require_finished: bool,
) -> ProgressState:
    value = _mapping(raw, "invalid_progress_receipt")
    if set(value) != _PROGRESS_KEYS or value.get("schema_version") != 1:
        raise ProgressReceiptError("invalid_progress_receipt")
    sequence = _nonnegative_int(value.get("sequence"), "invalid_progress_receipt")
    raw_state = value.get("state")
    active_node = value.get("active_node_id")
    phase = value.get("active_phase")
    if (
        sequence < 1
        or raw_state not in {"running", "finished"}
        or not isinstance(phase, str)
        or phase not in _PHASES
        or (active_node is not None and not isinstance(active_node, str))
        or (isinstance(active_node, str) and active_node not in selected)
    ):
        raise ProgressReceiptError("invalid_progress_receipt")
    state: Literal["running", "finished"] = "running" if raw_state == "running" else "finished"
    completed = _node_ids(value.get("completed_node_ids"), "invalid_progress_receipt")
    elapsed_raw = _mapping(value.get("node_elapsed_ns"), "invalid_progress_receipt")
    elapsed = {
        node_id: _nonnegative_int(duration, "invalid_progress_receipt")
        for node_id, duration in elapsed_raw.items()
    }
    if not set(completed).issubset(selected) or set(elapsed) != set(completed):
        raise ProgressReceiptError("invalid_progress_receipt")
    if require_finished and (
        state != "finished" or active_node is not None or phase != "session_finish"
    ):
        raise ProgressReceiptError("unfinished_progress_receipt")
    return ProgressState(
        schema_version=1,
        sequence=sequence,
        state=state,
        session_elapsed_ns=_nonnegative_int(
            value.get("session_elapsed_ns"), "invalid_progress_receipt"
        ),
        active_node_id=active_node,
        active_phase=phase,
        completed_node_ids=completed,
        node_elapsed_ns=elapsed,
    )


def load_progress_receipt(
    path: Path,
    *,
    require_finished: bool,
) -> PostgresTestReceipt:
    raw = _mapping(_read_json(path), "invalid_progress_receipt")
    if set(raw) != _RECEIPT_KEYS or raw.get("schema_version") != 1:
        raise ProgressReceiptError("invalid_progress_receipt")
    selected = _node_ids(raw.get("selected_node_ids"), "invalid_progress_receipt")
    executed = _node_ids(raw.get("executed_node_ids"), "invalid_progress_receipt")
    deselected = _node_ids(raw.get("deselected_node_ids"), "invalid_progress_receipt")
    failed = _node_ids(raw.get("failed_node_ids"), "invalid_progress_receipt")
    skipped = _node_ids(raw.get("skipped_node_ids"), "invalid_progress_receipt")
    digest = hashlib.sha256("\n".join(selected).encode()).hexdigest()
    if (
        not selected
        or raw.get("selected_sha256") != digest
        or not set(executed).issubset(selected)
        or not set(failed).issubset(selected)
        or not set(skipped).issubset(selected)
    ):
        raise ProgressReceiptError("invalid_progress_receipt")
    resource_raw = raw.get("resource_receipt")
    resource = _resource_receipt(resource_raw) if resource_raw is not None else None
    progress = _progress(raw.get("progress"), selected, require_finished)
    if progress["state"] == "finished" and (
        progress["active_node_id"] is not None
        or progress["active_phase"] != "session_finish"
        or resource is None
    ):
        raise ProgressReceiptError("invalid_progress_receipt")
    if progress["state"] == "running" and (
        progress["active_phase"] == "session_finish" or resource is not None
    ):
        raise ProgressReceiptError("invalid_progress_receipt")
    return PostgresTestReceipt(
        schema_version=1,
        selected_node_ids=selected,
        executed_node_ids=executed,
        deselected_node_ids=deselected,
        failed_node_ids=failed,
        skipped_node_ids=skipped,
        selected_sha256=digest,
        resource_receipt=resource,
        progress=progress,
    )


__all__ = ["PostgresTestReceipt", "ProgressReceiptError", "load_progress_receipt"]
