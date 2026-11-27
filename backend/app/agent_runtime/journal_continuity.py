"""Carry persistence loss from a bounded retry buffer to the chunk sequence."""

from typing import Any

JOURNAL_GAP_BEFORE = "_moldy_journal_gap_before"


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
