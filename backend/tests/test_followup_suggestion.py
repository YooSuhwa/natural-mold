from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from app.services.followup_service import (
    E2E_FOLLOWUP_SUGGESTION,
    _sanitize_suggestion,
    _transcript_tail,
)
from tests.conftest import TEST_USER_ID


async def _seed_conversation(db: AsyncSession) -> Conversation:
    user = await db.get(User, TEST_USER_ID)
    if user is None:
        user = User(id=TEST_USER_ID, email="followup@test.dev", name="Followup User")
        db.add(user)

    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()

    agent = Agent(
        user_id=user.id,
        name="Followup Agent",
        system_prompt="You are helpful.",
        model_id=model.id,
    )
    db.add(agent)
    await db.flush()

    conversation = Conversation(agent_id=agent.id, title="Followup Conversation")
    db.add(conversation)
    await db.commit()
    return conversation


class _Msg:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


def test_sanitize_strips_bullets_numbering_and_quotes() -> None:
    assert _sanitize_suggestion("- 표로 정리해줘") == "표로 정리해줘"
    assert _sanitize_suggestion("1. 표로 정리해줘") == "표로 정리해줘"
    assert _sanitize_suggestion('"표로 정리해줘"') == "표로 정리해줘"
    assert _sanitize_suggestion("```\n표로 정리해줘\n```") == "표로 정리해줘"


def test_sanitize_takes_first_line_and_caps_length() -> None:
    assert _sanitize_suggestion("첫 제안\n둘째 제안") == "첫 제안"
    long = "가" * 500
    sanitized = _sanitize_suggestion(long)
    assert sanitized is not None and len(sanitized) <= 120


def test_sanitize_empty_returns_none() -> None:
    assert _sanitize_suggestion("") is None
    assert _sanitize_suggestion("   \n  ") is None


def test_transcript_tail_filters_roles_and_limits() -> None:
    messages = [
        _Msg("system", "숨김"),
        _Msg("user", "질문1"),
        _Msg("tool", "도구 출력"),
        _Msg("assistant", "답변1"),
        _Msg("user", ""),
    ]
    tail = _transcript_tail(messages)
    assert tail == "사용자: 질문1\n어시스턴트: 답변1"
    assert _transcript_tail([_Msg("tool", "x")]) is None


@pytest.mark.asyncio
async def test_followup_endpoint_returns_scripted_suggestion(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "e2e_scripted_model_enabled", True)
    conversation = await _seed_conversation(db)

    response = await client.post(f"/api/conversations/{conversation.id}/followup-suggestion")

    assert response.status_code == 200
    assert response.json() == {"suggestion": E2E_FOLLOWUP_SUGGESTION}


@pytest.mark.asyncio
async def test_followup_endpoint_null_when_system_model_unconfigured(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "e2e_scripted_model_enabled", False)
    conversation = await _seed_conversation(db)

    response = await client.post(f"/api/conversations/{conversation.id}/followup-suggestion")

    assert response.status_code == 200
    assert response.json() == {"suggestion": None}


@pytest.mark.asyncio
async def test_followup_endpoint_unknown_conversation_404(client: AsyncClient) -> None:
    response = await client.post(f"/api/conversations/{uuid.uuid4()}/followup-suggestion")
    assert response.status_code == 404
