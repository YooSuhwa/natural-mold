"""SQLite-only pytest adapter for PostgreSQL lifecycle advisory locks."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import agent_lifecycle_locks

_REAL_ACQUIRE = agent_lifecycle_locks.acquire_agent_lifecycle_locks


async def acquire_agent_lifecycle_locks(db: AsyncSession, agent_ids: Iterable[UUID]) -> None:
    """Skip PostgreSQL advisory SQL only for the shared SQLite unit engine."""
    materialized_ids = tuple(agent_ids)
    if db.get_bind().dialect.name == "sqlite":
        return
    await _REAL_ACQUIRE(db, materialized_ids)


@pytest.fixture(autouse=True)
def sqlite_agent_lifecycle_lock_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the lock seam without changing metadata reads or assertions."""
    monkeypatch.setattr(
        agent_lifecycle_locks,
        "acquire_agent_lifecycle_locks",
        acquire_agent_lifecycle_locks,
    )
