"""Database engine ownership shutdown contract."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app import database


@pytest.mark.asyncio
async def test_shutdown_database_disposes_application_engine(monkeypatch) -> None:
    # Given the application engine owner.
    dispose = AsyncMock()
    engine = type("FakeEngine", (), {"dispose": dispose})()
    monkeypatch.setattr(database, "engine", engine)

    # When database shutdown runs.
    await database.shutdown_database()

    # Then the engine is explicitly disposed exactly once.
    dispose.assert_awaited_once_with()
