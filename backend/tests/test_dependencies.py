"""Tests for app.dependencies — get_db, get_current_user.

ADR-016 — ``get_current_user`` was rewritten to require a Request +
AsyncSession (JWT cookie/Bearer extraction). The legacy assertion that it
returned a hard-coded mock no longer applies; we now check that calling
without a token raises 401 ``not_authenticated``.
"""

from __future__ import annotations

import pytest
from fastapi import Request

from app.dependencies import get_current_user, get_db
from app.exceptions import AppError


@pytest.mark.asyncio
async def test_get_current_user_without_token_raises_401(db):
    """No cookie / no Bearer → ``not_authenticated`` AppError."""

    scope = {"type": "http", "headers": [], "method": "GET", "path": "/"}
    request = Request(scope)
    with pytest.raises(AppError) as exc:
        await get_current_user(request, db)
    assert exc.value.status == 401
    assert exc.value.code == "not_authenticated"


@pytest.mark.asyncio
async def test_get_db():
    """get_db yields an async session."""
    async for session in get_db():
        assert session is not None
        break
