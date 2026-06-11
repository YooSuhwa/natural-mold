"""Tests for app.agent_runtime.assistant.tools.write_tools — 18 DB-mutating tools.

The write tools internally create their own DB sessions via async_session_factory.
We monkeypatch that factory to use the test DB session maker throughout.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_trigger import AgentTrigger
from app.models.conversation import Conversation
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
        definition_key="builtin:web_search",
        description="Search the web",
    )
    db.add(tool)
    await db.flush()
    link = AgentToolLink(agent_id=agent.id, tool_id=tool.id)
    db.add(link)

    # Add a second tool (not linked to agent) for add_tool test
    tool2 = Tool(
        name="Web Scraper",
        definition_key="builtin:web_scraper",
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
    match = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        result,
    )
    assert match is not None
    return match.group(0)


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

    result = await tool.ainvoke(
        {
            "old_string": "helpful assistant",
            "new_string": "expert analyst",
        }
    )
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

    result = await tool.ainvoke(
        {
            "old_string": "nonexistent text that does not appear",
            "new_string": "replacement",
        }
    )
    assert "찾을 수 없습니다" in result


# ---------------------------------------------------------------------------
# update_agent_identity_mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_agent_identity_mode(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "update_agent_identity_mode")

    result = await tool.ainvoke({"identity_mode": "per_user"})
    assert "credential 사용 모드 변경 완료" in result

    read_tools = _build_read_tools(db, agent_id)
    config_tool = _find_tool(read_tools, "get_agent_config")
    config_result = await config_tool.ainvoke({})
    data = json.loads(config_result)
    assert data["identity_mode"] == "per_user"


@pytest.mark.asyncio
async def test_update_agent_identity_mode_rejects_per_user_with_active_schedule(
    db: AsyncSession, patch_write_session
):
    agent_id, _ = await _seed_full(db)
    db.add(
        AgentTrigger(
            agent_id=agent_id,
            user_id=TEST_USER_ID,
            name="Hourly",
            trigger_type="cron",
            schedule_config={"cron_expression": "0 * * * *"},
            input_message="run",
            status="active",
        )
    )
    await db.commit()

    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "update_agent_identity_mode")

    result = await tool.ainvoke({"identity_mode": "per_user"})
    assert "활성 스케줄" in result


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
# add_subagent_to_agent / remove_subagent_from_agent
# ---------------------------------------------------------------------------


async def _create_sibling_agent(db: AsyncSession, name: str = "Sibling Agent") -> uuid.UUID:
    """Create another agent owned by TEST_USER_ID. Assumes _seed_full already ran."""
    from sqlalchemy import select

    from app.models.model import Model

    result = await db.execute(select(Model).limit(1))
    model = result.scalar_one()

    sibling = Agent(
        user_id=TEST_USER_ID,
        name=name,
        description=f"{name} desc",
        system_prompt="sibling prompt",
        model_id=model.id,
    )
    db.add(sibling)
    await db.commit()
    await db.refresh(sibling)
    return sibling.id


@pytest.mark.asyncio
async def test_add_subagent_to_agent(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    sibling_id = await _create_sibling_agent(db, "Helper")

    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "add_subagent_to_agent")

    result = await tool.ainvoke({"agent_ids": [str(sibling_id)]})
    assert "추가 완료" in result
    assert "Helper" in result

    # Verify DB state
    from sqlalchemy import select

    from app.models.agent_subagent import AgentSubAgentLink

    res = await db.execute(
        select(AgentSubAgentLink).where(AgentSubAgentLink.parent_agent_id == agent_id)
    )
    links = res.scalars().all()
    assert len(links) == 1
    assert links[0].sub_agent_id == sibling_id


@pytest.mark.asyncio
async def test_add_subagent_self_reference_skipped(db: AsyncSession, patch_write_session):
    """Passing the agent's own id should be skipped, not raise."""
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "add_subagent_to_agent")

    result = await tool.ainvoke({"agent_ids": [str(agent_id)]})
    assert "자기 참조" in result


@pytest.mark.asyncio
async def test_add_subagent_invalid_uuid_skipped(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "add_subagent_to_agent")

    result = await tool.ainvoke({"agent_ids": ["not-a-uuid"]})
    assert "잘못된 UUID" in result


@pytest.mark.asyncio
async def test_remove_subagent_from_agent(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    sibling_id = await _create_sibling_agent(db, "ToRemove")

    tools = _build_write_tools(db, agent_id)
    add_tool = _find_tool(tools, "add_subagent_to_agent")
    await add_tool.ainvoke({"agent_ids": [str(sibling_id)]})

    remove_tool = _find_tool(tools, "remove_subagent_from_agent")
    result = await remove_tool.ainvoke({"agent_ids": [str(sibling_id)]})
    assert "제거 완료" in result
    assert "ToRemove" in result

    # Verify DB
    from sqlalchemy import select

    from app.models.agent_subagent import AgentSubAgentLink

    res = await db.execute(
        select(AgentSubAgentLink).where(AgentSubAgentLink.parent_agent_id == agent_id)
    )
    assert res.scalars().all() == []


@pytest.mark.asyncio
async def test_remove_subagent_not_linked(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "remove_subagent_from_agent")

    result = await tool.ainvoke({"agent_ids": [str(uuid.uuid4())]})
    assert "에이전트에 없습니다" in result


# ---------------------------------------------------------------------------
# add_skill_to_agent / remove_skill_from_agent
# ---------------------------------------------------------------------------


async def _create_skill(db: AsyncSession, name: str) -> uuid.UUID:
    from app.models.skill import Skill

    skill = Skill(
        user_id=TEST_USER_ID,
        name=name,
        slug=name.lower().replace(" ", "-"),
        description=f"{name} desc",
        kind="text",
        storage_path=f"/tmp/skills/{name.lower()}/SKILL.md",
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return skill.id


@pytest.mark.asyncio
async def test_add_skill_to_agent(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    await _create_skill(db, "MySkill")

    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "add_skill_to_agent")

    result = await tool.ainvoke({"skill_names": ["MySkill"]})
    assert "추가 완료" in result
    assert "MySkill" in result

    # Verify DB
    from sqlalchemy import select

    from app.models.skill import AgentSkillLink

    res = await db.execute(select(AgentSkillLink).where(AgentSkillLink.agent_id == agent_id))
    assert len(res.scalars().all()) == 1


@pytest.mark.asyncio
async def test_add_skill_to_agent_unknown(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "add_skill_to_agent")

    result = await tool.ainvoke({"skill_names": ["DoesNotExist"]})
    assert "찾을 수 없습니다" in result


@pytest.mark.asyncio
async def test_remove_skill_from_agent(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    await _create_skill(db, "Removable")

    tools = _build_write_tools(db, agent_id)
    add_tool = _find_tool(tools, "add_skill_to_agent")
    await add_tool.ainvoke({"skill_names": ["Removable"]})

    remove_tool = _find_tool(tools, "remove_skill_from_agent")
    result = await remove_tool.ainvoke({"skill_names": ["Removable"]})
    assert "제거 완료" in result
    assert "Removable" in result


@pytest.mark.asyncio
async def test_remove_skill_not_linked(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "remove_skill_from_agent")

    result = await tool.ainvoke({"skill_names": ["NotLinked"]})
    assert "에이전트에 없습니다" in result


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
    result = await tool.ainvoke(
        {
            "middleware_name": "summarization",
            "params": {"trigger": ["tokens", 8000]},
        }
    )
    assert "변경 완료" in result


@pytest.mark.asyncio
async def test_update_middleware_config_not_found(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "update_middleware_config")

    result = await tool.ainvoke(
        {
            "middleware_name": "nonexistent",
            "params": {"key": "val"},
        }
    )
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

    result = await tool.ainvoke(
        {
            "schedule_type": "recurring",
            "name": "매 시간 뉴스",
            "message": "매 시간 뉴스 검색",
            "cron_expression": "0 * * * *",
        }
    )
    assert "생성 완료" in result

    trigger = await db.get(AgentTrigger, uuid.UUID(_extract_schedule_id(result)))
    assert trigger is not None
    assert trigger.name == "매 시간 뉴스"
    assert trigger.trigger_type == "cron"
    assert trigger.schedule_config == {"cron_expression": "0 * * * *"}
    assert trigger.timezone == "Asia/Seoul"
    assert trigger.conversation_policy == "schedule_thread"


@pytest.mark.asyncio
async def test_create_cron_schedule_interval(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "create_cron_schedule")

    result = await tool.ainvoke(
        {
            "schedule_type": "interval",
            "name": "10분 모니터링",
            "message": "상태 확인",
            "interval_minutes": 10,
            "timezone": "Asia/Seoul",
            "conversation_policy": "schedule_thread",
            "max_runs": 3,
            "auto_pause_after_failures": 2,
        }
    )
    assert "생성 완료" in result

    trigger = await db.get(AgentTrigger, uuid.UUID(_extract_schedule_id(result)))
    assert trigger is not None
    assert trigger.name == "10분 모니터링"
    assert trigger.trigger_type == "interval"
    assert trigger.schedule_config == {"interval_minutes": 10}
    assert trigger.timezone == "Asia/Seoul"
    assert trigger.conversation_policy == "schedule_thread"
    assert trigger.max_runs == 3
    assert trigger.auto_pause_after_failures == 2


@pytest.mark.asyncio
async def test_create_cron_schedule_one_time(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "create_cron_schedule")

    # 하드코딩 날짜는 시간이 지나면 "scheduled_at must be in the future" 검증에
    # 걸려 테스트가 부패한다 — 항상 미래인 시각을 동적으로 생성.
    scheduled_at = (datetime.now() + timedelta(days=1)).replace(microsecond=0).isoformat()
    result = await tool.ainvoke(
        {
            "schedule_type": "one_time",
            "message": "내일 리포트",
            "scheduled_at": scheduled_at,
        }
    )
    assert "생성 완료" in result


@pytest.mark.asyncio
async def test_create_cron_schedule_missing_cron(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "create_cron_schedule")

    result = await tool.ainvoke(
        {
            "schedule_type": "recurring",
            "message": "test",
        }
    )
    assert "cron_expression이 필요" in result


@pytest.mark.asyncio
async def test_create_cron_schedule_missing_scheduled_at(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "create_cron_schedule")

    result = await tool.ainvoke(
        {
            "schedule_type": "one_time",
            "message": "test",
        }
    )
    assert "scheduled_at이 필요" in result


@pytest.mark.asyncio
async def test_create_cron_schedule_invalid_type(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "create_cron_schedule")

    result = await tool.ainvoke(
        {
            "schedule_type": "invalid",
            "message": "test",
        }
    )
    assert "'recurring' 또는 'one_time'" in result


@pytest.mark.asyncio
async def test_create_cron_schedule_invalid_cron_expression(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "create_cron_schedule")

    result = await tool.ainvoke(
        {
            "schedule_type": "recurring",
            "message": "test",
            "cron_expression": "invalid cron",
        }
    )
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
    create_result = await create_tool.ainvoke(
        {
            "schedule_type": "recurring",
            "message": "매 시간 검색",
            "cron_expression": "0 * * * *",
        }
    )
    # Extract ID
    schedule_id = _extract_schedule_id(create_result)

    update_tool = _find_tool(tools, "update_cron_schedule")
    result = await update_tool.ainvoke(
        {
            "schedule_id": schedule_id,
            "cron_expression": "30 * * * *",
            "message": "30분마다 검색",
        }
    )
    assert "수정 완료" in result

    trigger = await db.get(AgentTrigger, uuid.UUID(schedule_id))
    assert trigger is not None
    assert trigger.schedule_config == {"cron_expression": "30 * * * *"}
    assert trigger.input_message == "30분마다 검색"


@pytest.mark.asyncio
async def test_update_cron_schedule_validates_uuid_and_datetime_strings(
    db: AsyncSession, patch_write_session
):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)

    conversation = Conversation(agent_id=agent_id, title="선택 대화")
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)

    create_tool = _find_tool(tools, "create_cron_schedule")
    create_result = await create_tool.ainvoke(
        {
            "schedule_type": "recurring",
            "message": "매 시간 검색",
            "cron_expression": "0 * * * *",
        }
    )
    schedule_id = _extract_schedule_id(create_result)

    update_tool = _find_tool(tools, "update_cron_schedule")
    result = await update_tool.ainvoke(
        {
            "schedule_id": schedule_id,
            "conversation_policy": "selected_conversation",
            "target_conversation_id": str(conversation.id),
            "end_at": "2035-01-01T00:00:00+09:00",
        }
    )
    assert "수정 완료" in result

    trigger = await db.get(AgentTrigger, uuid.UUID(schedule_id))
    assert trigger is not None
    assert trigger.conversation_policy == "selected_conversation"
    assert trigger.target_conversation_id == conversation.id
    assert trigger.end_at is not None
    assert trigger.end_at.isoformat() == "2034-12-31T15:00:00"


@pytest.mark.asyncio
async def test_update_cron_schedule_by_name_requires_unique_match(
    db: AsyncSession, patch_write_session
):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)
    create_tool = _find_tool(tools, "create_cron_schedule")
    update_tool = _find_tool(tools, "update_cron_schedule")

    for message in ("첫 번째", "두 번째"):
        result = await create_tool.ainvoke(
            {
                "schedule_type": "recurring",
                "name": "아침 뉴스",
                "message": message,
                "cron_expression": "0 9 * * *",
            }
        )
        assert "생성 완료" in result

    result = await update_tool.ainvoke(
        {
            "schedule_name": "아침 뉴스",
            "cron_expression": "30 9 * * *",
        }
    )
    assert "여러 개" in result
    assert "ID" in result


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
    create_result = await create_tool.ainvoke(
        {
            "schedule_type": "recurring",
            "message": "삭제 테스트",
            "cron_expression": "0 * * * *",
        }
    )
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
    create_result = await create_tool.ainvoke(
        {
            "schedule_type": "recurring",
            "message": "활성화 테스트",
            "cron_expression": "0 * * * *",
        }
    )
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


@pytest.mark.asyncio
async def test_enable_cron_schedule_returns_fixed_identity_error(
    db: AsyncSession, patch_write_session
):
    agent_id, _ = await _seed_full(db)
    agent = await db.get(Agent, agent_id)
    assert agent is not None
    agent.identity_mode = "per_user"
    trigger = AgentTrigger(
        agent_id=agent_id,
        user_id=TEST_USER_ID,
        name="Paused",
        trigger_type="cron",
        schedule_config={"cron_expression": "0 * * * *"},
        input_message="run",
        status="paused",
    )
    db.add(trigger)
    await db.commit()

    tools = _build_write_tools(db, agent_id)
    tool = _find_tool(tools, "enable_cron_schedule")

    result = await tool.ainvoke({"schedule_id": str(trigger.id)})
    assert "스케줄 설정이 올바르지 않습니다" in result
    assert "identity_mode must be fixed" in result


# ---------------------------------------------------------------------------
# disable_cron_schedule
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_cron_schedule(db: AsyncSession, patch_write_session):
    agent_id, _ = await _seed_full(db)
    tools = _build_write_tools(db, agent_id)

    create_tool = _find_tool(tools, "create_cron_schedule")
    create_result = await create_tool.ainvoke(
        {
            "schedule_type": "recurring",
            "message": "비활성화 테스트",
            "cron_expression": "0 * * * *",
        }
    )
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
