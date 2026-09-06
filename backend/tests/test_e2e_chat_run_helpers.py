"""Unit coverage for the E2E chat-run helper's user gate."""

from __future__ import annotations

import uuid

import pytest

from app.config import settings
from app.dependencies import CurrentUser
from app.exceptions import NotFoundError
from app.routers.e2e_chat_run_helpers import _require_e2e_user


def _user(email: str) -> CurrentUser:
    return CurrentUser(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        email=email,
        name="E2E Test User",
    )


def test_configured_e2e_user_passes_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    configured_email = "playwright-e2e@moldy.test"
    monkeypatch.setattr(settings, "e2e_user_email", configured_email)

    assert _require_e2e_user(_user(configured_email)) is None


def test_other_user_gets_enumeration_safe_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    configured_email = "playwright-e2e@moldy.test"
    monkeypatch.setattr(settings, "e2e_user_email", configured_email)

    with pytest.raises(NotFoundError) as exc_info:
        _require_e2e_user(_user("another-user@moldy.test"))

    assert exc_info.value.status == 404
    assert exc_info.value.code == "CONVERSATION_NOT_FOUND"
    assert exc_info.value.message == "대화를 찾을 수 없습니다"
