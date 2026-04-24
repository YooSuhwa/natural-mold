"""Tests for app.agent_runtime.assistant.tools.write_tools — 18 DB-mutating tools.

The write tools internally create their own DB sessions via async_session_factory.
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
    """Create User + Model + Agent + Tool + AgentToolLink. Return (agent_id, tool_id)."""
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
        name="Write Test Agent",
        description="Agent for write tool tests",
        system_prompt="You are a helpful assistant. Use tools wisely.",
        model_id=model.id,
        model_params={"temperature": 0.7},
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

    # Add a second tool (not linked to agent) for add_tool test
    tool2 = Tool(
        name="Web Scraper",
        type="prebuilt",
        is_system=True,
        description="Scrape web pages",
    )
    db.add(tool2)
    await db.commit()
    return agent.id, tool.id


def _find_tool(tools, name: str):
    for t in tools:
        if t.name == name:
            return t
    raise ValueError(f"Tool '{name}' not found")


@pytest.fixture
def patch_write_session():
    """Patch async_session_factory in write_tools + read_tools modules."""
    with (
        patch(
            "app.agent_runtime.assistant.tools.write_tools.async_session_factory",
            TestSession,
        ),
        patch(
            "app.agent_runtime.assistant.tools.read_tools.async_session_factory",
            TestSession,
        ),
    ):
        yield


def _extract_schedule_id(result: str) -> str:
    """Extract schedule ID from create_cron_schedule result string."""
    return result.split("ID: ")[1].rstrip(")")


def _build_write_tools(db: AsyncSession, agent_id: uuid.UUID):
    from app.agent_runtime.assistant.tools.write_tools import build_write_tools

    return build_write_tools(db, agent_id, TEST_USER_ID)


def _build_read_tools(db: AsyncSession, agent_id: uuid.UUID):
    from app.agent_runtime.assistant.tools.read_tools import build_read_tools

    return build_read_tools(db, agent_id, TEST_USER_ID)


# ---------------------------------------------------------------------------
# add_tool_to_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_tool_to_agent(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "add_tool_to_agent")

    result = await tool.ainvoke({"tool_names": ["Web Scraper"]})
    assert "추가 완료" in result
    assert "Web Scraper" in result


# ---------------------------------------------------------------------------
# remove_tool_from_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_tool_from_agent(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "remove_tool_from_agent")

    result = await tool.ainvoke({"tool_names": ["Web Search"]})
    assert "제거 완료" in result
    assert "Web Search" in result


# ---------------------------------------------------------------------------
# edit_system_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_system_prompt(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "edit_system_prompt")

    result = await tool.ainvoke({
        "old_string": "helpful assistant",
        "new_string": "expert analyst",
    })
    assert "수정 완료" in result

    # Verify the change persisted via read tool
    read_tools = _build_read_tools(db, agent_id)
    config_tool = _find_tool(read_tools, "get_agent_config")
    config_result = await config_tool.ainvoke({})
    data = json.loads(config_result)
    assert "expert analyst" in data["system_prompt"]


@pytest.mark.asyncio
async def test_edit_system_prompt_not_found(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "edit_system_prompt")

    result = await tool.ainvoke({
        "old_string": "nonexistent text that does not appear",
        "new_string": "replacement",
    })
    assert "찾을 수 없습니다" in result


# ---------------------------------------------------------------------------
# update_system_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_system_prompt(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "update_system_prompt")

    result = await tool.ainvoke({"new_system_prompt": "Brand new prompt."})
    assert "전체 교체 완료" in result

    # Verify
    read_tools = _build_read_tools(db, agent_id)
    config_tool = _find_tool(read_tools, "get_agent_config")
    config_result = await config_tool.ainvoke({})
    data = json.loads(config_result)
    assert data["system_prompt"] == "Brand new prompt."


# ---------------------------------------------------------------------------
# update_model_config — valid range
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_model_config(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "update_model_config")

    result = await tool.ainvoke({"temperature": 1.5})
    assert "변경 완료" in result
    assert "temperature: 1.5" in result


# ---------------------------------------------------------------------------
# update_model_config — invalid ranges
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_model_config_invalid(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "update_model_config")

    # Temperature out of range
    result = await tool.ainvoke({"temperature": 3.0})
    assert "0.0~2.0" in result

    # top_p out of range
    result = await tool.ainvoke({"top_p": 1.5})
    assert "0.0~1.0" in result

    # max_tokens negative
    result = await tool.ainvoke({"max_tokens": -1})
    assert "양수" in result


# ---------------------------------------------------------------------------
# add_middleware_to_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_middleware_to_agent(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "add_middleware_to_agent")

    result = await tool.ainvoke({"middleware_names": ["summarization"]})
    assert "추가 완료" in result
    assert "summarization" in result


@pytest.mark.asyncio
async def test_add_middleware_already_exists(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "add_middleware_to_agent")

    # First add
    await tool.ainvoke({"middleware_names": ["summarization"]})
    # Second add should say already exists
    result = await tool.ainvoke({"middleware_names": ["summarization"]})
    assert "추가할 미들웨어가 없습니다" in result


@pytest.mark.asyncio
async def test_add_middleware_unknown(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "add_middleware_to_agent")

    result = await tool.ainvoke({"middleware_names": ["nonexistent_mw"]})
    assert "추가할 미들웨어가 없습니다" in result


# ---------------------------------------------------------------------------
# remove_middleware_from_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_middleware_from_agent(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)

    # Add first, then remove
    add_tool = _find_tool(tools, "add_middleware_to_agent")
    await add_tool.ainvoke({"middleware_names": ["summarization"]})

    remove_tool = _find_tool(tools, "remove_middleware_from_agent")
    result = await remove_tool.ainvoke({"middleware_names": ["summarization"]})
    assert "제거 완료" in result
    assert "summarization" in result


@pytest.mark.asyncio
async def test_remove_middleware_not_found(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "remove_middleware_from_agent")

    result = await tool.ainvoke({"middleware_names": ["nonexistent"]})
    assert "해당 미들웨어가 없습니다" in result


# ---------------------------------------------------------------------------
# add_subagent_to_agent / remove_subagent_from_agent (stubs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_subagent_stub(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "add_subagent_to_agent")

    result = await tool.ainvoke({"agent_ids": ["some-id"]})
    assert "구현되지 않았습니다" in result


@pytest.mark.asyncio
async def test_remove_subagent_stub(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "remove_subagent_from_agent")

    result = await tool.ainvoke({"agent_ids": ["some-id"]})
    assert "구현되지 않았습니다" in result


# ---------------------------------------------------------------------------
# update_middleware_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_middleware_config(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)

    # Add middleware first
    add_tool = _find_tool(tools, "add_middleware_to_agent")
    await add_tool.ainvoke({"middleware_names": ["summarization"]})

    tool = _find_tool(tools, "update_middleware_config")
    result = await tool.ainvoke({
        "middleware_name": "summarization",
        "params": {"trigger": ["tokens", 8000]},
    })
    assert "변경 완료" in result


@pytest.mark.asyncio
async def test_update_middleware_config_not_found(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "update_middleware_config")

    result = await tool.ainvoke({
        "middleware_name": "nonexistent",
        "params": {"key": "val"},
    })
    assert "찾을 수 없습니다" in result


# ---------------------------------------------------------------------------
# update_chat_openers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_chat_openers(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "update_chat_openers")

    result = await tool.ainvoke({"openers": ["안녕하세요", "도움이 필요하세요?"]})
    assert "2개 설정 완료" in result


# ---------------------------------------------------------------------------
# update_recursion_limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_recursion_limit(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "update_recursion_limit")

    result = await tool.ainvoke({"limit": 50})
    assert "50" in result
    assert "변경" in result


@pytest.mark.asyncio
async def test_update_recursion_limit_out_of_range(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "update_recursion_limit")

    result = await tool.ainvoke({"limit": 5})
    assert "10~200" in result

    result = await tool.ainvoke({"limit": 300})
    assert "10~200" in result


# ---------------------------------------------------------------------------
# create_cron_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_cron_schedule_recurring(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "create_cron_schedule")

    result = await tool.ainvoke({
        "schedule_type": "recurring",
        "message": "매 시간 뉴스 검색",
        "cron_expression": "0 * * * *",
    })
    assert "생성 완료" in result


@pytest.mark.asyncio
async def test_create_cron_schedule_one_time(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "create_cron_schedule")

    result = await tool.ainvoke({
        "schedule_type": "one_time",
        "message": "내일 리포트",
        "scheduled_at": "2026-04-08T09:00:00",
    })
    assert "생성 완료" in result


@pytest.mark.asyncio
async def test_create_cron_schedule_missing_cron(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "create_cron_schedule")

    result = await tool.ainvoke({
        "schedule_type": "recurring",
        "message": "test",
    })
    assert "cron_expression이 필요" in result


@pytest.mark.asyncio
async def test_create_cron_schedule_missing_scheduled_at(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "create_cron_schedule")

    result = await tool.ainvoke({
        "schedule_type": "one_time",
        "message": "test",
    })
    assert "scheduled_at이 필요" in result


@pytest.mark.asyncio
async def test_create_cron_schedule_invalid_type(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "create_cron_schedule")

    result = await tool.ainvoke({
        "schedule_type": "invalid",
        "message": "test",
    })
    assert "'recurring' 또는 'one_time'" in result


@pytest.mark.asyncio
async def test_create_cron_schedule_invalid_cron_expression(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "create_cron_schedule")

    result = await tool.ainvoke({
        "schedule_type": "recurring",
        "message": "test",
        "cron_expression": "invalid cron",
    })
    assert "유효하지 않은 cron 표현식" in result


# ---------------------------------------------------------------------------
# update_cron_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_cron_schedule(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)

    # Create first
    create_tool = _find_tool(tools, "create_cron_schedule")
    create_result = await create_tool.ainvoke({
        "schedule_type": "recurring",
        "message": "매 시간 검색",
        "cron_expression": "0 * * * *",
    })
    # Extract ID
    schedule_id = _extract_schedule_id(create_result)

    update_tool = _find_tool(tools, "update_cron_schedule")
    result = await update_tool.ainvoke({
        "schedule_id": schedule_id,
        "cron_expression": "30 * * * *",
        "message": "30분마다 검색",
    })
    assert "수정 완료" in result


@pytest.mark.asyncio
async def test_update_cron_schedule_invalid_id(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "update_cron_schedule")

    result = await tool.ainvoke({"schedule_id": "not-a-uuid"})
    assert "유효하지 않은 스케줄 ID" in result


@pytest.mark.asyncio
async def test_update_cron_schedule_not_found(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "update_cron_schedule")

    result = await tool.ainvoke({"schedule_id": str(uuid.uuid4())})
    assert "찾을 수 없습니다" in result


# ---------------------------------------------------------------------------
# delete_cron_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_cron_schedule(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)

    create_tool = _find_tool(tools, "create_cron_schedule")
    create_result = await create_tool.ainvoke({
        "schedule_type": "recurring",
        "message": "삭제 테스트",
        "cron_expression": "0 * * * *",
    })
    schedule_id = _extract_schedule_id(create_result)

    delete_tool = _find_tool(tools, "delete_cron_schedule")
    result = await delete_tool.ainvoke({"schedule_id": schedule_id})
    assert "삭제 완료" in result


@pytest.mark.asyncio
async def test_delete_cron_schedule_invalid_id(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "delete_cron_schedule")

    result = await tool.ainvoke({"schedule_id": "bad-id"})
    assert "유효하지 않은 스케줄 ID" in result


@pytest.mark.asyncio
async def test_delete_cron_schedule_not_found(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "delete_cron_schedule")

    result = await tool.ainvoke({"schedule_id": str(uuid.uuid4())})
    assert "찾을 수 없습니다" in result


# ---------------------------------------------------------------------------
# enable_cron_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enable_cron_schedule(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)

    create_tool = _find_tool(tools, "create_cron_schedule")
    create_result = await create_tool.ainvoke({
        "schedule_type": "recurring",
        "message": "활성화 테스트",
        "cron_expression": "0 * * * *",
    })
    schedule_id = _extract_schedule_id(create_result)

    tool = _find_tool(tools, "enable_cron_schedule")
    result = await tool.ainvoke({"schedule_id": schedule_id})
    assert "활성화 완료" in result


@pytest.mark.asyncio
async def test_enable_cron_schedule_invalid_id(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "enable_cron_schedule")

    result = await tool.ainvoke({"schedule_id": "not-uuid"})
    assert "유효하지 않은 스케줄 ID" in result


@pytest.mark.asyncio
async def test_enable_cron_schedule_not_found(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "enable_cron_schedule")

    result = await tool.ainvoke({"schedule_id": str(uuid.uuid4())})
    assert "찾을 수 없습니다" in result


# ---------------------------------------------------------------------------
# disable_cron_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_cron_schedule(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)

    create_tool = _find_tool(tools, "create_cron_schedule")
    create_result = await create_tool.ainvoke({
        "schedule_type": "recurring",
        "message": "비활성화 테스트",
        "cron_expression": "0 * * * *",
    })
    schedule_id = _extract_schedule_id(create_result)

    tool = _find_tool(tools, "disable_cron_schedule")
    result = await tool.ainvoke({"schedule_id": schedule_id})
    assert "비활성화 완료" in result


@pytest.mark.asyncio
async def test_disable_cron_schedule_invalid_id(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "disable_cron_schedule")

    result = await tool.ainvoke({"schedule_id": "not-uuid"})
    assert "유효하지 않은 스케줄 ID" in result


@pytest.mark.asyncio
async def test_disable_cron_schedule_not_found(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "disable_cron_schedule")

    result = await tool.ainvoke({"schedule_id": str(uuid.uuid4())})
    assert "찾을 수 없습니다" in result
