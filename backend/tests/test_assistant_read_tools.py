"""Tests for app.agent_runtime.assistant.tools.read_tools — 16 safe read tools.

The read tools internally create their own DB sessions via async_session_factory.
We monkeypatch that factory to use the test DB session maker throughout.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.model import Model
from app.models.tool import AgentToolLink, Tool
from app.models.user import User
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_full(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Create User + Model + Agent + Tool + AgentToolLink. Return (agent_id, model_id)."""
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    model = Model(
        provider="openai",
        model_name="gpt-4o",
        display_name="GPT-4o",
        is_default=True,
    )
    db.add(model)
    await db.flush()
    agent = Agent(
        user_id=user.id,
        name="Read Test Agent",
        description="Agent for read tool tests",
        system_prompt="You are a helpful assistant.\nUse tools wisely.\nBe concise.",
        model_id=model.id,
        model_params={"temperature": 0.7, "recursion_limit": 30, "chat_openers": ["안녕하세요"]},
    )
    db.add(agent)
    await db.flush()
    tool = Tool(
        name="Web Search",
        type="prebuilt",
        is_system=True,
        description="Search the web",
    )
    db.add(tool)
    await db.flush()
    link = AgentToolLink(agent_id=agent.id, tool_id=tool.id)
    db.add(link)
    await db.commit()
    return agent.id, model.id


def _find_tool(tools, name: str):
    for t in tools:
        if t.name == name:
            return t
    raise ValueError(f"Tool '{name}' not found")


@pytest.fixture
def patch_read_session():
    """Patch async_session_factory in the read_tools module for the entire test."""
    with patch(
        "app.agent_runtime.assistant.tools.read_tools.async_session_factory",
        TestSession,
    ):
        yield


def _build_tools(db: AsyncSession, agent_id: uuid.UUID):
    """Build read tools."""
    from app.agent_runtime.assistant.tools.read_tools import build_read_tools

    return build_read_tools(db, agent_id, TEST_USER_ID)


# ---------------------------------------------------------------------------
# get_agent_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_config(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "get_agent_config")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert data["name"] == "Read Test Agent"
    assert data["agent_id"] == str(agent_id)
    assert len(data["tools"]) == 1
    assert data["tools"][0]["name"] == "Web Search"
    assert "gpt-4o" in data["model_name"]


# ---------------------------------------------------------------------------
# get_model_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_model_config(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "get_model_config")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert "gpt-4o" in data["model_name"]
    assert data["model_params"]["temperature"] == 0.7


# ---------------------------------------------------------------------------
# list_available_tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_available_tools(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "list_available_tools")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert isinstance(data, list)
    assert len(data) >= 1
    names = [item["name"] for item in data]
    assert "Web Search" in names


# ---------------------------------------------------------------------------
# list_available_middlewares
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_available_middlewares(db: AsyncSession):
    """list_available_middlewares doesn't need DB patch (reads from registry)."""
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "list_available_middlewares")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert isinstance(data, list)
    assert len(data) > 0
    types = [item["type"] for item in data]
    assert "summarization" in types


# ---------------------------------------------------------------------------
# list_available_models
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_available_models(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "list_available_models")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0]["display_name"] == "GPT-4o"


# ---------------------------------------------------------------------------
# search_system_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_system_prompt(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "search_system_prompt")

    result = await tool.ainvoke({"keyword": "tools"})
    data = json.loads(result)
    assert data["found"] is True
    assert len(data["matches"]) >= 1
    assert "tools" in data["matches"][0]["text"].lower()


@pytest.mark.asyncio
async def test_search_system_prompt_not_found(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "search_system_prompt")

    result = await tool.ainvoke({"keyword": "zzzznotexist"})
    data = json.loads(result)
    assert data["found"] is False
    assert len(data["matches"]) == 0


# ---------------------------------------------------------------------------
# get_recursion_limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_recursion_limit(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "get_recursion_limit")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert data["recursion_limit"] == 30


# ---------------------------------------------------------------------------
# list_available_subagents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_available_subagents(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "list_available_subagents")

    result = await tool.ainvoke({})
    data = json.loads(result)
    # Our agent is the only one, and it should be excluded from subagent list
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.asyncio
async def test_list_available_subagents_with_other_agent(db: AsyncSession, patch_read_session):
    """When another agent exists, it should appear in the subagent list."""
    agent_id, model_id = await _seed_full(db)

    # Create another agent
    other_agent = Agent(
        user_id=TEST_USER_ID,
        name="Other Agent",
        description="Another agent",
        system_prompt="system",
        model_id=model_id,
    )
    db.add(other_agent)
    await db.commit()

    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "list_available_subagents")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert len(data) == 1
    assert data[0]["name"] == "Other Agent"


# ---------------------------------------------------------------------------
# get_agent_required_secrets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_required_secrets(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "get_agent_required_secrets")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert "required" in data
    assert "missing" in data
    # Web Search doesn't need special keys
    assert data["required"] == []


@pytest.mark.asyncio
async def test_get_agent_required_secrets_with_naver_tool(db: AsyncSession, patch_read_session):
    """Naver tool should require NAVER_CLIENT_ID and NAVER_CLIENT_SECRET."""
    agent_id, _ = await _seed_full(db)

    # Add naver tool
    naver_tool = Tool(
        name="Naver Search",
        type="prebuilt",
        is_system=True,
        description="Naver search",
    )
    db.add(naver_tool)
    await db.flush()
    link = AgentToolLink(agent_id=agent_id, tool_id=naver_tool.id)
    db.add(link)
    await db.commit()

    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "get_agent_required_secrets")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert "NAVER_CLIENT_ID" in data["required"]
    assert "NAVER_CLIENT_SECRET" in data["required"]


# ---------------------------------------------------------------------------
# get_user_secrets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_user_secrets(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "get_user_secrets")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert data["secrets"] == []


# ---------------------------------------------------------------------------
# get_chat_openers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_chat_openers(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "get_chat_openers")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert data["chat_openers"] == ["안녕하세요"]


# ---------------------------------------------------------------------------
# list_permanent_files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_permanent_files(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "list_permanent_files")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert data["files"] == []


# ---------------------------------------------------------------------------
# get_file_content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_file_content(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "get_file_content")

    result = await tool.ainvoke({"file_id": "some-file-id"})
    assert "찾을 수 없습니다" in result


# ---------------------------------------------------------------------------
# list_cron_schedules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_cron_schedules_empty(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "list_cron_schedules")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.asyncio
async def test_list_cron_schedules_with_trigger(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)

    # Create a trigger
    from app.models.agent_trigger import AgentTrigger

    trigger = AgentTrigger(
        agent_id=agent_id,
        user_id=TEST_USER_ID,
        trigger_type="cron",
        schedule_config={"type": "cron", "expression": "0 * * * *"},
        input_message="테스트 메시지",
    )
    db.add(trigger)
    await db.commit()

    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "list_cron_schedules")

    result = await tool.ainvoke({})
    data = json.loads(result)
    assert len(data) == 1
    assert data[0]["type"] == "cron"
    assert data[0]["message"] == "테스트 메시지"


# ---------------------------------------------------------------------------
# get_cron_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cron_schedule(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)

    from app.models.agent_trigger import AgentTrigger

    trigger = AgentTrigger(
        agent_id=agent_id,
        user_id=TEST_USER_ID,
        trigger_type="cron",
        schedule_config={"type": "cron", "expression": "30 * * * *"},
        input_message="상세 조회 테스트",
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)

    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "get_cron_schedule")

    result = await tool.ainvoke({"schedule_id": str(trigger.id)})
    data = json.loads(result)
    assert data["type"] == "cron"
    assert data["message"] == "상세 조회 테스트"


@pytest.mark.asyncio
async def test_get_cron_schedule_invalid_id(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "get_cron_schedule")

    result = await tool.ainvoke({"schedule_id": "not-a-uuid"})
    assert "유효하지 않은 스케줄 ID" in result


@pytest.mark.asyncio
async def test_get_cron_schedule_not_found(db: AsyncSession, patch_read_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_tools(db, agent_id)
    tool = _find_tool(tools, "get_cron_schedule")

    result = await tool.ainvoke({"schedule_id": str(uuid.uuid4())})
    assert "찾을 수 없습니다" in result
