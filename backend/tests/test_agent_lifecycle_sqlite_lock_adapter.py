"""The unit-suite adapter skips only SQLite advisory lock acquisition."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests import agent_lifecycle_sqlite_lock_adapter as adapter


@dataclass(frozen=True, slots=True)
class _Dialect:
    name: str


@dataclass(frozen=True, slots=True)
class _Bind:
    dialect: _Dialect


async def test_sqlite_adapter_skips_delegate(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    delegated: list[tuple[UUID, ...]] = []

    async def delegate(_db: AsyncSession, agent_ids: tuple[UUID, ...]) -> None:
        delegated.append(agent_ids)

    monkeypatch.setattr(adapter, "_REAL_ACQUIRE", delegate)

    await adapter.acquire_agent_lifecycle_locks(db, (uuid4(),))

    assert delegated == []


async def test_non_sqlite_adapter_delegates_unchanged(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    delegated: list[tuple[UUID, ...]] = []
    agent_ids = (uuid4(), uuid4())

    async def delegate(_db: AsyncSession, received: tuple[UUID, ...]) -> None:
        delegated.append(received)

    monkeypatch.setattr(adapter, "_REAL_ACQUIRE", delegate)
    monkeypatch.setattr(db, "get_bind", lambda: _Bind(_Dialect("postgresql")))

    await adapter.acquire_agent_lifecycle_locks(db, agent_ids)

    assert delegated == [agent_ids]
