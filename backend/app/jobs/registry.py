"""Job types and their handlers.

A feature registers a handler for its job type at import time; the worker runs only the
types it has handlers for. A handler gets a ``JobContext`` (the job, a company session
factory, progress and cancel checks) and returns the job's result (JSON) or raises.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from app.jobs.context import JobContext

Handler = Callable[["JobContext"], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True, slots=True)
class JobType:
    name: str
    handler: Handler
    # How long a claim lasts without a heartbeat before another worker may take the job.
    lease_seconds: int = 120
    max_attempts: int = 3


class JobFailed(Exception):
    """A handler's known failure; ``retry=False`` ends the job without another attempt."""

    def __init__(self, code: str, message: str, *, retry: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retry = retry


class JobCanceled(Exception):
    """Raised by ``JobContext.check_canceled`` once someone asked to cancel the job."""


_TYPES: dict[str, JobType] = {}
MAX_NAME: Final = 64


def register(job_type: JobType) -> JobType:
    if not 0 < len(job_type.name) <= MAX_NAME:
        raise ValueError(f"job type name must be 1-{MAX_NAME} characters")
    if job_type.name in _TYPES and _TYPES[job_type.name] is not job_type:
        raise ValueError(f"job type {job_type.name!r} is already registered")
    _TYPES[job_type.name] = job_type
    return job_type


def get(name: str) -> JobType | None:
    return _TYPES.get(name)


def names() -> tuple[str, ...]:
    return tuple(sorted(_TYPES))
