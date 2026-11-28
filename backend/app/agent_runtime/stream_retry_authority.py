"""Execution-owner admission hook inherited by asynchronous model operations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from langgraph.runtime import ExecutionInfo


class StreamAttemptDenied(PermissionError):
    """The captured worker no longer owns an active run."""

    def __init__(self) -> None:
        super().__init__("stream_attempt_denied")


type AdmissionCheck = Callable[[bool], Awaitable[None]]

_check: ContextVar[AdmissionCheck | None] = ContextVar("stream_attempt_check", default=None)


@dataclass(frozen=True, slots=True)
class ModelAttempt:
    execution: ExecutionInfo | None
    limit: int


_attempt: ContextVar[ModelAttempt | None] = ContextVar("direct_model_attempt", default=None)


@contextmanager
def model_attempt_scope(execution: ExecutionInfo | None, limit: int) -> Iterator[None]:
    """Carry graph-owned identity into the authority check for exactly one send."""
    token = _attempt.set(ModelAttempt(execution, limit))
    try:
        yield
    finally:
        _attempt.reset(token)


def current_model_attempt() -> ModelAttempt | None:
    return _attempt.get()


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
