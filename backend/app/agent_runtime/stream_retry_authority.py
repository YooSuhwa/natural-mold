"""Execution-owner admission hook inherited by asynchronous model operations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar


class StreamAttemptDenied(PermissionError):
    """The captured worker no longer owns an active run."""

    def __init__(self) -> None:
        super().__init__("stream_attempt_denied")


type AdmissionCheck = Callable[[bool], Awaitable[None]]

_check: ContextVar[AdmissionCheck | None] = ContextVar("stream_attempt_check", default=None)


@contextmanager
def bind_stream_attempt_check(check: AdmissionCheck) -> Iterator[None]:
    token = _check.set(check)
    try:
        yield
    finally:
        _check.reset(token)


async def check_stream_attempt(*, prepared: bool = False) -> None:
    task = asyncio.current_task()
    if task is not None and task.cancelling():
        raise asyncio.CancelledError
    check = _check.get()
    if check is not None:
        await check(prepared)
