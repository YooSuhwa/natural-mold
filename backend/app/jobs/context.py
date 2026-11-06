"""What a job handler gets: its job, a company session factory, progress and cancel."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.registry import JobCanceled


@dataclass(slots=True)
class JobContext:
    job_id: uuid.UUID
    tenant_id: uuid.UUID
    org_id: uuid.UUID
    actor_id: uuid.UUID | None
    type: str
    attempt: int
    payload: dict[str, Any]
    # A session in the job's company only (RLS); the handler never sees another company.
    session: Callable[[], AsyncSession]
    _progress: Callable[[int, str | None], Any] = field(repr=False)
    _cancel_requested: bool = field(default=False, repr=False)

    async def progress(self, percent: int, note: str | None = None) -> None:
        """Record progress now (also extends the lease and reads a cancel request)."""
        await self._progress(percent, note)

    def check_canceled(self) -> None:
        if self._cancel_requested:
            raise JobCanceled
