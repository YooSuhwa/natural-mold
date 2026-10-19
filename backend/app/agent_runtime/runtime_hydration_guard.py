"""Post-marker hydration guard (parent Task 10, internal I15).

While a prepared lease executes (after the ``runtime_materialized_at`` marker),
no named preparation/hydration seam may run: component/agent preparation, MCP
discovery, credential resolution, memory loading, global checkpointer access or
initial history loading. Each seam calls :func:`assert_pre_marker`; the runner
enters :func:`post_marker_execution` for the lifetime of the lease execution.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

HYDRATION_DENIED = "post_marker_hydration_denied"
_ACTIVE_LEASE_RUN: ContextVar[str | None] = ContextVar("moldy_prepared_lease_run", default=None)


class HydrationDeniedError(RuntimeError):
    code = HYDRATION_DENIED

    def __init__(self, seam: str) -> None:
        super().__init__(f"{HYDRATION_DENIED}:{seam}")
        self.seam = seam


def assert_pre_marker(seam: str) -> None:
    """Fail closed when a preparation seam is reached during lease execution."""

    if _ACTIVE_LEASE_RUN.get() is not None:
        raise HydrationDeniedError(seam)


def active_lease_run() -> str | None:
    return _ACTIVE_LEASE_RUN.get()


@contextmanager
def post_marker_execution(run_id: str) -> Iterator[None]:
    token = _ACTIVE_LEASE_RUN.set(run_id)
    try:
        yield
    finally:
        _ACTIVE_LEASE_RUN.reset(token)
