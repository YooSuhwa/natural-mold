"""Tests for app.dependencies — get_db, get_current_user."""

from __future__ import annotations

import uuid

import pytest

from app.dependencies import CurrentUser, get_current_user, get_db


@pytest.mark.asyncio
async def test_get_current_user():
    """get_current_user returns a mock CurrentUser."""
    user = await get_current_user()
    assert isinstance(user, CurrentUser)
    assert isinstance(user.id, uuid.UUID)
    assert user.email
    assert user.name


@pytest.mark.asyncio
async def test_get_db():
    """get_db yields an async session."""
    async for session in get_db():
        assert session is not None
        break
