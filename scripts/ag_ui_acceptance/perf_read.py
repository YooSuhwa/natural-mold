"""Measure durable projection costs for a marked, synthetic slow-stream run."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any
from unittest.mock import patch

_MARKER = re.compile(r"\bE2E_AGUI_PERF_([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})\b")
_COSTS: ContextVar[dict[str, int] | None] = ContextVar("ag_ui_test_read_costs", default=None)
_SERVICES = (
    "require_read_conversation",
    "resolve_read_target",
    "collect_conversation_secret_values",
    "read_journal_page",
    "current_read_interrupts",
    "committed_run_changed",
)


type AsyncOperation = Callable[..., Awaitable[Any]]


def _measure_service(fn: AsyncOperation, name: str) -> AsyncOperation:
    @wraps(fn)
    async def measure(*args: Any, **kwargs: Any) -> Any:
        costs = _COSTS.get()
        if costs is None:
            return await fn(*args, **kwargs)
        start = time.monotonic_ns()
        try:
            return await fn(*args, **kwargs)
        finally:
            costs[name] = costs.get(name, 0) + time.monotonic_ns() - start

    return measure


def _measure_reader(
    fn: AsyncOperation, name: str, fd: int, parts: Mapping[str, tuple[int, ...]]
) -> AsyncOperation:
    @wraps(fn)
    async def measure(reader: Any, *args: Any, **kwargs: Any) -> Any:
        costs = {}
        token = _COSTS.set(costs)
        start = time.monotonic_ns()
        epoch_start = time.time_ns()
        try:
            value = await fn(reader, *args, **kwargs)
        finally:
            _COSTS.reset(token)
        end = time.monotonic_ns()
        epoch_end = time.time_ns()
        target = reader.target
        # Access only already-loaded scalar values; the observer never performs a query.
        run = target.run if target is not None else None
        preview = vars(run).get("input_preview") if run is not None else None
        if not isinstance(preview, str) or "E2E_SLOW_STREAM" not in preview:
            return value
        sample = _MARKER.search(preview)
        if not sample:
            return value
        row = {
            "sample_id": sample[1],
            "run_id": str(vars(run).get("id")),
            "operation": name,
            "start_epoch_ms": epoch_start / 1_000_000,
            "end_epoch_ms": epoch_end / 1_000_000,
            "duration_ms": (end - start) / 1_000_000,
            "stages_ms": {key: round(ns / 1_000_000, 3) for key, ns in costs.items()},
        }
        frames = ()
        if name == "poll":
            frames = value.frames
        elif name == "check_delivery":
            frames = args[:1]

        row["frame_count"] = len(frames)
        # Keep ordinals of the fixed test-model strings only. Never log content or event data.
        row["text_ordinals"] = [
            ordinal
            for frame in frames
            if frame.event.get("type") == "TEXT_MESSAGE_CONTENT"
            and not frame.event.get("subagentRunId")
            for ordinal in parts.get(frame.event["delta"], ())
        ]
        os.write(fd, (json.dumps(row, separators=(",", ":")) + "\n").encode("ascii"))
        return value

    return measure


@contextmanager
def observe_read_durations(ledger: Path) -> Iterator[None]:
    if not (
        ledger.is_absolute()
        and os.environ.get("E2E_LANE") == "scripted"
        and os.environ.get("E2E_SCRIPTED_MODEL_ENABLED") == "true"
        and os.environ.get("MOLDY_DISABLE_ENV_FILE") == "true"
        and os.environ.get("APP_ENV", "dev") == "dev"
    ):
        raise ValueError("Read timing requires the isolated scripted acceptance lane")
    from app.agent_runtime.e2e_scripted_model import SLOW_STREAM_PARTS
    from app.services import ag_ui_read

    # Several committed provider tokens may be projected in a single text frame.
    parts = {
        "".join(SLOW_STREAM_PARTS[start:stop]): tuple(range(start + 1, stop + 1))
        for start in range(len(SLOW_STREAM_PARTS))
        for stop in range(start + 1, len(SLOW_STREAM_PARTS) + 1)
    }
    with ExitStack() as stack:
        fd = os.open(ledger, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        stack.callback(os.close, fd)
        for name in _SERVICES:
            fn = getattr(ag_ui_read, name, None)
            if fn is None:
                continue
            stack.enter_context(patch.object(ag_ui_read, name, _measure_service(fn, name)))
        for name in ("poll", "check_delivery", "wait_for_change"):
            fn = getattr(ag_ui_read._Reader, name, None)
            if fn is None:
                continue
            stack.enter_context(
                patch.object(ag_ui_read._Reader, name, _measure_reader(fn, name, fd, parts))
            )
        yield
