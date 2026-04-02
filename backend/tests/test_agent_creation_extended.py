"""Tests for agent creation — router + service layer."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_creation_session import AgentCreationSession
from app.models.model import Model
from app.models.tool import Tool
from app.models.user import User
from app.services import agent_creation_service
from tests.conftest import TEST_USER_ID, TestSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user() -> None:
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        await db.commit()


async def _seed_session(
    *,
    status: str = "in_progress",
    draft_config: dict | None = None,
    history: list | None = None,
) -> uuid.UUID:
    async with TestSession() as db:
        session = AgentCreationSession(
            user_id=TEST_USER_ID,
            status=status,
            conversation_history=history or [],
            draft_config=draft_config,
        )
        db.add(session)
        await db.commit()
        return session.id


async def _seed_model(display_name: str = "GPT-4o", *, is_default: bool = False) -> uuid.UUID:
    async with TestSession() as db:
        model = Model(
            provider="openai",
            model_name="gpt-4o",
            display_name=display_name,
            is_default=is_default,
        )
        db.add(model)
        await db.commit()
        return model.id


async def _seed_tool(name: str = "Web Search") -> uuid.UUID:
    async with TestSession() as db:
        tool = Tool(
            name=name,
            type="builtin",
            is_system=True,
            description=f"{name} tool",
        )
        db.add(tool)
        await db.commit()
        return tool.id


# ===========================================================================
# Router tests
# ===========================================================================


@pytest.mark.asyncio
async def test_start_creation_session(client: AsyncClient):
    await _seed_user()
    resp = await client.post("/api/agents/create-session")
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "in_progress"
    assert data["conversation_history"] == []
    assert data["draft_config"] is None


@pytest.mark.asyncio
async def test_get_session(client: AsyncClient):
    await _seed_user()
    session_id = await _seed_session()
    resp = await client.get(f"/api/agents/create-session/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(session_id)


@pytest.mark.asyncio
async def test_get_session_not_found(client: AsyncClient):
    fake_id = "00000000-0000-0000-0000-000000000099"
    resp = await client.get(f"/api/agents/create-session/{fake_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_send_creation_message(client: AsyncClient):
    await _seed_user()
    session_id = await _seed_session()

    mock_response = {
        "role": "assistant",
        "content": "에이전트를 만들어 보겠습니다.",
        "raw_content": "에이전트를 만들어 보겠습니다.\n```json\n{}\n```",
        "current_phase": 2,
        "phase_result": "Phase 1 완료",
        "question": "어떤 에이전트를 만들고 싶으세요?",
        "draft_config": None,
        "suggested_replies": {"options": ["뉴스 검색", "직접 입력"], "multi_select": False},
        "recommended_tools": [],
    }

    with patch(
        "app.services.agent_creation_service.run_creation_conversation",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        resp = await client.post(
            f"/api/agents/create-session/{session_id}/message",
            json={"content": "뉴스 검색 에이전트 만들어줘"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "assistant"
    assert data["current_phase"] == 2


@pytest.mark.asyncio
async def test_send_message_session_completed(client: AsyncClient):
    await _seed_user()
    session_id = await _seed_session(status="completed")

    resp = await client.post(
        f"/api/agents/create-session/{session_id}/message",
        json={"content": "test"},
    )
    assert resp.status_code == 400
    assert "not in progress" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_confirm_creation_success(client: AsyncClient):
    await _seed_user()
    await _seed_model(is_default=True)

    draft = {
        "name": "뉴스 에이전트",
        "description": "뉴스를 검색합니다",
        "system_prompt": "You search news.",
        "recommended_tool_names": [],
        "recommended_model": "GPT-4o",
    }
    session_id = await _seed_session(draft_config=draft)

    resp = await client.post(f"/api/agents/create-session/{session_id}/confirm")
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "뉴스 에이전트"
    assert data["system_prompt"] == "You search news."


@pytest.mark.asyncio
async def test_confirm_creation_no_draft_config(client: AsyncClient):
    await _seed_user()
    session_id = await _seed_session(draft_config=None)

    resp = await client.post(f"/api/agents/create-session/{session_id}/confirm")
    assert resp.status_code == 400
    assert "No draft config" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_confirm_creation_no_model_returns_400(client: AsyncClient):
    """If no model exists at all, confirm should return 400."""
    await _seed_user()
    draft = {
        "name": "Test Agent",
        "system_prompt": "Hi",
        "recommended_model": "NonExistent Model",
    }
    session_id = await _seed_session(draft_config=draft)

    resp = await client.post(f"/api/agents/create-session/{session_id}/confirm")
    assert resp.status_code == 400
    assert "Could not create" in resp.json()["detail"]


# ===========================================================================
# Service-level tests
# ===========================================================================


@pytest.mark.asyncio
async def test_service_create_session(db: AsyncSession):
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    await db.commit()

    session = await agent_creation_service.create_session(db, TEST_USER_ID)
    assert session.status == "in_progress"
    assert session.conversation_history == []
    assert session.id is not None


@pytest.mark.asyncio
async def test_service_get_session(db: AsyncSession):
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    s = AgentCreationSession(user_id=TEST_USER_ID, conversation_history=[])
    db.add(s)
    await db.commit()

    found = await agent_creation_service.get_session(db, s.id, TEST_USER_ID)
    assert found is not None
    assert found.id == s.id


@pytest.mark.asyncio
async def test_service_get_session_not_found(db: AsyncSession):
    result = await agent_creation_service.get_session(db, uuid.uuid4(), TEST_USER_ID)
    assert result is None


@pytest.mark.asyncio
async def test_service_send_message_updates_history(db: AsyncSession):
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    session = AgentCreationSession(user_id=TEST_USER_ID, conversation_history=[])
    db.add(session)
    await db.commit()

    mock_response = {
        "role": "assistant",
        "content": "Hello",
        "raw_content": "Hello raw",
        "current_phase": 1,
        "phase_result": None,
        "question": None,
        "draft_config": None,
        "suggested_replies": None,
        "recommended_tools": [],
    }

    with patch(
        "app.services.agent_creation_service.run_creation_conversation",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await agent_creation_service.send_message(db, session, "user msg")

    assert result["role"] == "assistant"
    # Session history should have user + assistant messages
    assert len(session.conversation_history) == 2
    assert session.conversation_history[0]["role"] == "user"
    assert session.conversation_history[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_service_send_message_updates_draft_config(db: AsyncSession):
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    session = AgentCreationSession(user_id=TEST_USER_ID, conversation_history=[])
    db.add(session)
    await db.commit()

    draft = {"name": "My Agent", "system_prompt": "Hi", "is_ready": True}
    mock_response = {
        "role": "assistant",
        "content": "Done",
        "raw_content": "Done",
        "current_phase": 4,
        "phase_result": None,
        "question": None,
        "draft_config": draft,
        "suggested_replies": None,
        "recommended_tools": [],
    }

    with patch(
        "app.services.agent_creation_service.run_creation_conversation",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        await agent_creation_service.send_message(db, session, "확인")

    assert session.draft_config is not None
    assert session.draft_config["name"] == "My Agent"


@pytest.mark.asyncio
async def test_service_confirm_finds_model_by_name(db: AsyncSession):
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    model = Model(
        provider="openai", model_name="gpt-4o", display_name="GPT-4o", is_default=False
    )
    db.add(model)
    await db.flush()

    session = AgentCreationSession(
        user_id=TEST_USER_ID,
        conversation_history=[],
        draft_config={
            "name": "Named Model Agent",
            "system_prompt": "Hi",
            "recommended_model": "GPT-4o",
        },
    )
    db.add(session)
    await db.commit()

    agent = await agent_creation_service.confirm_creation(db, session)
    assert agent is not None
    assert agent.name == "Named Model Agent"


@pytest.mark.asyncio
async def test_service_confirm_falls_back_to_default_model(db: AsyncSession):
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    # Model with different display name, but is_default=True
    model = Model(
        provider="anthropic", model_name="claude-3", display_name="Claude 3", is_default=True
    )
    db.add(model)
    await db.flush()

    session = AgentCreationSession(
        user_id=TEST_USER_ID,
        conversation_history=[],
        draft_config={
            "name": "Fallback Agent",
            "system_prompt": "Hi",
            "recommended_model": "NonExistent Model",
        },
    )
    db.add(session)
    await db.commit()

    agent = await agent_creation_service.confirm_creation(db, session)
    assert agent is not None
    assert agent.name == "Fallback Agent"


@pytest.mark.asyncio
async def test_service_confirm_no_model_returns_none(db: AsyncSession):
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    await db.flush()

    session = AgentCreationSession(
        user_id=TEST_USER_ID,
        conversation_history=[],
        draft_config={
            "name": "No Model Agent",
            "system_prompt": "Hi",
            "recommended_model": "Ghost",
        },
    )
    db.add(session)
    await db.commit()

    agent = await agent_creation_service.confirm_creation(db, session)
    assert agent is None


@pytest.mark.asyncio
async def test_service_confirm_links_tools_by_name(db: AsyncSession):
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    model = Model(
        provider="openai", model_name="gpt-4o", display_name="GPT-4o", is_default=True
    )
    db.add(model)
    await db.flush()

    tool = Tool(name="Web Search", type="builtin", is_system=True, description="Search")
    db.add(tool)
    await db.flush()

    session = AgentCreationSession(
        user_id=TEST_USER_ID,
        conversation_history=[],
        draft_config={
            "name": "Tool Agent",
            "system_prompt": "Search",
            "recommended_tool_names": ["web search"],  # lowercase — case-insensitive match
            "recommended_model": "GPT-4o",
        },
    )
    db.add(session)
    await db.commit()

    agent = await agent_creation_service.confirm_creation(db, session)
    assert agent is not None
    assert len(agent.tool_links) == 1


@pytest.mark.asyncio
async def test_service_confirm_sets_completed_status(db: AsyncSession):
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    model = Model(
        provider="openai", model_name="gpt-4o", display_name="GPT-4o", is_default=True
    )
    db.add(model)
    await db.flush()

    session = AgentCreationSession(
        user_id=TEST_USER_ID,
        conversation_history=[],
        draft_config={"name": "Done Agent", "system_prompt": "Hi"},
    )
    db.add(session)
    await db.commit()

    await agent_creation_service.confirm_creation(db, session)
    assert session.status == "completed"
