"""Run-scoped journal commit timing and bounded retry continuity."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

JOURNAL_GAP_BEFORE = "_moldy_journal_gap_before"
_AG_UI_COMMIT_INTERVAL = 0.20
_ag_ui_run: ContextVar[str | None] = ContextVar("ag_ui_journal_run", default=None)


@contextmanager
def ag_ui_journal_scope(run_id: str, *, version: object) -> Iterator[None]:
    """Only a worker running a server-admitted v1 command opts into timed commits."""
    token = _ag_ui_run.set(run_id if version == "1" else None)
    try:
        yield
    finally:
        _ag_ui_run.reset(token)


def journal_commit_interval(run_id: str) -> float | None:
    return _AG_UI_COMMIT_INTERVAL if _ag_ui_run.get() == run_id else None


def trim_journal_retry_buffer(events: list[dict[str, Any]], limit: int) -> int:
    """Drop the oldest events and annotate a persistence-only copy of the survivor."""
    if limit < 1:
        raise ValueError("The journal retry buffer must retain at least one event")
    overflow = len(events) - limit
    if overflow <= 0:
        return 0
    del events[:overflow]
    # The original dict can still belong to the live trace or broker.
    events[0] = {**events[0], JOURNAL_GAP_BEFORE: True}
    return overflow
