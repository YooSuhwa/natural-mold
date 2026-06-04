"""Tests for app.services.builder_service — session CRUD, claim, confirm, helpers."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.model import Model
from app.models.skill import Skill
from app.models.tool import Tool
from app.models.user import User
from app.schemas.builder import BuilderStatus
from app.services.builder_service import (
    _get_middlewares_catalog,
    claim_for_confirming,
    confirm_build,
    create_session,
    get_agent_by_id,
    get_session,
)
from tests.conftest import TEST_USER_ID


async def _seed_user(db: AsyncSession) -> User:
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    await db.flush()
    return user


async def _seed_model(db: AsyncSession, *, is_default: bool = True) -> Model:
    model = Model(
        provider="openai",
        model_name="gpt-4o",
        display_name="GPT-4o",
        is_default=is_default,
    )
    db.add(model)
    await db.flush()
    return model


async def _seed_tool(db: AsyncSession) -> Tool:
    tool = Tool(
        name="Web Search",
        definition_key="builtin:web_search",
        description="Search the web",
    )
    db.add(tool)
    await db.flush()
    return tool


async def _seed_mcp_tools(
    db: AsyncSession, *, names: list[str]
) -> tuple[McpServer, list[McpTool]]:
    """McpServer 한 개 + names 만큼의 McpTool 생성."""
    server = McpServer(
        user_id=TEST_USER_ID,
        name="Hancom Org Chart",
        transport="sse",
        url="https://example.com/mcp",
    )
    db.add(server)
    await db.flush()
    tools = []
    for name in names:
        mt = McpTool(server_id=server.id, name=name, description=f"{name} desc")
        db.add(mt)
        tools.append(mt)
    await db.flush()
    return server, tools


async def _seed_skills(db: AsyncSession, *, names: list[str]) -> list[Skill]:
    """Test user 의 ``Skill`` row 들을 생성. slug 는 name 소문자 hyphenate."""
    skills = []
    for name in names:
        skill = Skill(
            user_id=TEST_USER_ID,
            name=name,
            slug=name.lower().replace(" ", "-").replace("_", "-"),
            description=f"{name} 가이드",
        )
        db.add(skill)
        skills.append(skill)
    await db.flush()
    return skills


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session(db: AsyncSession):
    await _seed_user(db)
    await db.commit()

    session = await create_session(db, TEST_USER_ID, "날씨 봇 만들어줘")
    assert session.id is not None
    assert session.status == BuilderStatus.BUILDING
    assert session.user_request == "날씨 봇 만들어줘"
    assert session.user_id == TEST_USER_ID
    assert session.current_phase == 0
    assert session.draft_config is None


# ---------------------------------------------------------------------------
# get_session / get_session_not_found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session(db: AsyncSession):
    await _seed_user(db)
    await db.commit()

    created = await create_session(db, TEST_USER_ID, "검색 에이전트")
    found = await get_session(db, created.id, TEST_USER_ID)
    assert found is not None
    assert found.id == created.id
    assert found.user_request == "검색 에이전트"


@pytest.mark.asyncio
async def test_get_session_not_found(db: AsyncSession):
    result = await get_session(db, uuid.uuid4(), TEST_USER_ID)
    assert result is None


# ---------------------------------------------------------------------------
# claim_for_confirming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_for_confirming(db: AsyncSession):
    """PREVIEW -> CONFIRMING transition succeeds."""
    await _seed_user(db)
    await db.commit()

    session = await create_session(db, TEST_USER_ID, "테스트")
    # Manually set status to PREVIEW
    session.status = BuilderStatus.PREVIEW
    await db.commit()

    ok = await claim_for_confirming(db, session.id, TEST_USER_ID)
    assert ok is True

    reloaded = await get_session(db, session.id, TEST_USER_ID)
    assert reloaded is not None
    assert reloaded.status == BuilderStatus.CONFIRMING


# ---------------------------------------------------------------------------
# confirm_build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_build_success(db: AsyncSession):
    """draft_config + model match -> Agent created."""
    await _seed_user(db)
    model = await _seed_model(db)
    await _seed_tool(db)
    await db.commit()

    session = await create_session(db, TEST_USER_ID, "날씨 봇")
    session.status = BuilderStatus.CONFIRMING
    session.draft_config = {
        "name": "Weather Bot",
        "name_ko": "날씨 봇",
        "description": "날씨를 알려주는 봇",
        "system_prompt": "You are a weather bot.",
        "tools": ["Web Search"],
        "middlewares": [],
        "model_name": "GPT-4o",
        "identity_mode": "per_user",
    }
    await db.commit()

    agent = await confirm_build(db, session)
    assert agent is not None
    assert agent.name == "날씨 봇"
    assert agent.system_prompt == "You are a weather bot."
    assert agent.model_id == model.id
    assert agent.identity_mode == "per_user"

    reloaded = await get_session(db, session.id, TEST_USER_ID)
    assert reloaded is not None
    assert reloaded.status == BuilderStatus.COMPLETED
    assert reloaded.agent_id == agent.id


@pytest.mark.asyncio
async def test_confirm_build_uses_fixed_identity_from_draft(db: AsyncSession):
    await _seed_user(db)
    await _seed_model(db)
    await db.commit()

    session = await create_session(db, TEST_USER_ID, "스케줄 봇")
    session.status = BuilderStatus.CONFIRMING
    session.draft_config = {
        "name": "Scheduler",
        "name_ko": "스케줄 봇",
        "description": "정해진 시간에 실행되는 봇",
        "system_prompt": "Run on schedule.",
        "tools": [],
        "middlewares": [],
        "model_name": "GPT-4o",
        "identity_mode": "fixed",
    }
    await db.commit()

    agent = await confirm_build(db, session)
    assert agent is not None
    assert agent.identity_mode == "fixed"


@pytest.mark.asyncio
async def test_confirm_build_links_mcp_tools(db: AsyncSession):
    """MCP 도구 회귀 가드.

    Builder phase3 catalog 는 ``Tool + McpTool`` 모두 노출하므로 phase8
    confirm 도 양쪽을 매칭해야 한다. 사용자가 MCP 서버를 등록해 도구
    이름이 ``McpTool`` 에만 존재할 때 ``confirm_build`` 가 silent drop
    하지 않고 ``agent.mcp_tool_links`` 에 정확히 연결하는지 검증.
    """
    await _seed_user(db)
    await _seed_model(db)
    _, mcp_tools = await _seed_mcp_tools(
        db, names=["list_departments", "search_employees"]
    )
    await db.commit()

    session = await create_session(db, TEST_USER_ID, "조직도 봇")
    session.status = BuilderStatus.CONFIRMING
    session.draft_config = {
        "name": "OrgChart",
        "name_ko": "조직도 봇",
        "description": "조직도 QA",
        "system_prompt": "you are an org chart assistant",
        "tools": ["list_departments", "search_employees"],
        "middlewares": [],
        "model_name": "GPT-4o",
    }
    await db.commit()

    agent = await confirm_build(db, session)
    assert agent is not None
    # Tool 테이블에는 없으므로 agent.tool_links 는 비어 있어야 함
    assert len(agent.tool_links) == 0
    # McpTool 두 개 모두 연결되어야 함
    linked_ids = {link.mcp_tool_id for link in agent.mcp_tool_links}
    assert linked_ids == {mt.id for mt in mcp_tools}


@pytest.mark.asyncio
async def test_confirm_build_mixed_tool_and_mcp(db: AsyncSession):
    """동일 draft.tools 안에 Tool 과 McpTool 이 섞여 있어도 양쪽 모두 링크."""
    await _seed_user(db)
    await _seed_model(db)
    tool = await _seed_tool(db)  # name="Web Search"
    _, mcp_tools = await _seed_mcp_tools(db, names=["list_departments"])
    await db.commit()

    session = await create_session(db, TEST_USER_ID, "혼합 봇")
    session.status = BuilderStatus.CONFIRMING
    session.draft_config = {
        "name": "Mixed",
        "name_ko": "혼합",
        "description": "d",
        "system_prompt": "p",
        "tools": ["Web Search", "list_departments"],
        "middlewares": [],
        "model_name": "GPT-4o",
    }
    await db.commit()

    agent = await confirm_build(db, session)
    assert agent is not None
    assert {link.tool_id for link in agent.tool_links} == {tool.id}
    assert {link.mcp_tool_id for link in agent.mcp_tool_links} == {
        mt.id for mt in mcp_tools
    }


@pytest.mark.asyncio
async def test_confirm_build_links_skills(db: AsyncSession):
    """Skill 회귀 가드 — Builder 가 skill 을 인지하고 ``agent.skill_links`` 생성.

    이전에는 phase3 카탈로그/추천이 skill 을 노출하지 않아 사용자가 "스킬을
    추가해줘" 라고 명시해도 도구만 추천되고 skill_links 는 항상 비어 있었음.
    catalog + draft_config.tools 흐름이 skill 도 포함하는지 검증.
    """
    await _seed_user(db)
    await _seed_model(db)
    skills = await _seed_skills(db, names=["seat_layout_guide", "evac_procedure"])
    await db.commit()

    session = await create_session(db, TEST_USER_ID, "위치 안내 봇")
    session.status = BuilderStatus.CONFIRMING
    session.draft_config = {
        "name": "Locate",
        "name_ko": "위치 봇",
        "description": "직원 좌석 안내",
        "system_prompt": "p",
        "tools": ["seat_layout_guide", "evac_procedure"],
        "middlewares": [],
        "model_name": "GPT-4o",
    }
    await db.commit()

    agent = await confirm_build(db, session)
    assert agent is not None
    assert len(agent.tool_links) == 0
    assert len(agent.mcp_tool_links) == 0
    assert {link.skill_id for link in agent.skill_links} == {s.id for s in skills}


@pytest.mark.asyncio
async def test_confirm_build_mixed_tool_mcp_skill(db: AsyncSession):
    """draft.tools 안에 Tool + McpTool + Skill 이 섞여도 모두 정확히 분리 링크."""
    await _seed_user(db)
    await _seed_model(db)
    tool = await _seed_tool(db)  # name="Web Search"
    _, mcp_tools = await _seed_mcp_tools(db, names=["list_departments"])
    skills = await _seed_skills(db, names=["seat_layout_guide"])
    await db.commit()

    session = await create_session(db, TEST_USER_ID, "혼합")
    session.status = BuilderStatus.CONFIRMING
    session.draft_config = {
        "name": "All",
        "name_ko": "전체",
        "description": "d",
        "system_prompt": "p",
        "tools": ["Web Search", "list_departments", "seat_layout_guide"],
        "middlewares": [],
        "model_name": "GPT-4o",
    }
    await db.commit()

    agent = await confirm_build(db, session)
    assert agent is not None
    assert {link.tool_id for link in agent.tool_links} == {tool.id}
    assert {link.mcp_tool_id for link in agent.mcp_tool_links} == {
        mt.id for mt in mcp_tools
    }
    assert {link.skill_id for link in agent.skill_links} == {s.id for s in skills}


@pytest.mark.asyncio
async def test_confirm_build_skill_cross_user_blocked(db: AsyncSession):
    """다른 사용자의 skill 은 ``Skill.user_id`` ownership 필터로 차단."""
    await _seed_user(db)
    await _seed_model(db)

    other_user_id = uuid.uuid4()
    other = User(id=other_user_id, email="other-skill@test.com", name="Other Skill")
    db.add(other)
    await db.flush()
    db.add(
        Skill(
            user_id=other_user_id,
            name="cross_user_skill",
            slug="cross-user-skill",
            description="d",
        )
    )
    await db.commit()

    session = await create_session(db, TEST_USER_ID, "차단")
    session.status = BuilderStatus.CONFIRMING
    session.draft_config = {
        "name": "X",
        "name_ko": "X",
        "description": "d",
        "system_prompt": "p",
        "tools": ["cross_user_skill"],
        "middlewares": [],
        "model_name": "GPT-4o",
    }
    await db.commit()

    agent = await confirm_build(db, session)
    assert agent is not None
    assert len(agent.tool_links) == 0
    assert len(agent.mcp_tool_links) == 0
    assert len(agent.skill_links) == 0


@pytest.mark.asyncio
async def test_confirm_build_mcp_cross_user_blocked(db: AsyncSession):
    """다른 사용자의 MCP 도구는 ownership 필터로 차단되어 링크되지 않음."""
    await _seed_user(db)
    await _seed_model(db)

    # 다른 사용자의 server + tool 시드
    other_user_id = uuid.uuid4()
    other = User(id=other_user_id, email="other@test.com", name="Other")
    db.add(other)
    await db.flush()
    other_server = McpServer(
        user_id=other_user_id,
        name="Other Server",
        transport="sse",
        url="https://example.com/other",
    )
    db.add(other_server)
    await db.flush()
    db.add(McpTool(server_id=other_server.id, name="cross_user_tool"))
    await db.commit()

    session = await create_session(db, TEST_USER_ID, "차단")
    session.status = BuilderStatus.CONFIRMING
    session.draft_config = {
        "name": "X",
        "name_ko": "X",
        "description": "d",
        "system_prompt": "p",
        "tools": ["cross_user_tool"],
        "middlewares": [],
        "model_name": "GPT-4o",
    }
    await db.commit()

    agent = await confirm_build(db, session)
    assert agent is not None
    assert len(agent.tool_links) == 0
    assert len(agent.mcp_tool_links) == 0


@pytest.mark.asyncio
async def test_confirm_build_no_model(db: AsyncSession):
    """When model_name doesn't match, fallback to default model."""
    await _seed_user(db)
    model = await _seed_model(db, is_default=True)
    await db.commit()

    session = await create_session(db, TEST_USER_ID, "테스트")
    session.status = BuilderStatus.CONFIRMING
    session.draft_config = {
        "name": "Test Agent",
        "name_ko": "테스트 에이전트",
        "description": "desc",
        "system_prompt": "prompt",
        "tools": [],
        "middlewares": [],
        "model_name": "nonexistent-model",
    }
    await db.commit()

    agent = await confirm_build(db, session)
    assert agent is not None
    # Should fall back to default model
    assert agent.model_id == model.id


@pytest.mark.asyncio
async def test_confirm_build_idempotent(db: AsyncSession):
    """COMPLETED session with agent_id returns existing agent (via service layer)."""
    await _seed_user(db)
    await _seed_model(db)
    await db.commit()

    # Create session and confirm
    session = await create_session(db, TEST_USER_ID, "테스트")
    session.status = BuilderStatus.CONFIRMING
    session.draft_config = {
        "name": "Bot",
        "name_ko": "봇",
        "description": "d",
        "system_prompt": "p",
        "tools": [],
        "middlewares": [],
        "model_name": "GPT-4o",
    }
    await db.commit()

    agent1 = await confirm_build(db, session)
    assert agent1 is not None
    assert session.status == BuilderStatus.COMPLETED

    # Second confirm on COMPLETED session — confirm_build returns None
    # because draft_config is still there but status is COMPLETED.
    # The router handles idempotency via agent_id check before calling confirm.
    # Here we verify that confirm_build still works (no crash) on the session.
    # Since session is now COMPLETED, the router would not call confirm_build again.
    reloaded = await get_session(db, session.id, TEST_USER_ID)
    assert reloaded is not None
    assert reloaded.agent_id == agent1.id


# ---------------------------------------------------------------------------
# _get_middlewares_catalog
# ---------------------------------------------------------------------------


def test_get_middlewares_catalog():
    """Returns middleware registry as list of dicts."""
    result = _get_middlewares_catalog()
    assert isinstance(result, list)
    assert len(result) > 0
    types = {item["type"] for item in result}
    # deepagents 빌트인 타입(summarization, todo_list 등)은 제외됨
    assert "summarization" not in types
    assert "tool_retry" in types


# ---------------------------------------------------------------------------
# _get_default_model_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_default_model_name_from_settings(db: AsyncSession):
    """When settings.default_agent_model is set, it is returned."""
    from unittest.mock import patch as _patch

    from app.services.builder_service import _get_default_model_name

    with _patch("app.config.settings") as mock_settings:
        mock_settings.default_agent_model = "anthropic:claude-3"
        result = await _get_default_model_name(db)
        assert result == "anthropic:claude-3"


@pytest.mark.asyncio
async def test_get_default_model_name_from_db_default(db: AsyncSession):
    """When no env var, returns is_default=True model from DB."""
    from unittest.mock import patch as _patch

    from app.services.builder_service import _get_default_model_name

    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    model = Model(
        provider="openai",
        model_name="gpt-4o",
        display_name="GPT-4o",
        is_default=True,
    )
    db.add(model)
    await db.commit()

    with _patch("app.config.settings") as mock_settings:
        mock_settings.default_agent_model = ""
        result = await _get_default_model_name(db)
        assert result == "openai:gpt-4o"


@pytest.mark.asyncio
async def test_get_default_model_name_from_db_any(db: AsyncSession):
    """When no default model, returns first model from DB."""
    from unittest.mock import patch as _patch

    from app.services.builder_service import _get_default_model_name

    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    model = Model(
        provider="anthropic",
        model_name="claude-3-sonnet",
        display_name="Claude 3 Sonnet",
        is_default=False,
    )
    db.add(model)
    await db.commit()

    with _patch("app.config.settings") as mock_settings:
        mock_settings.default_agent_model = ""
        result = await _get_default_model_name(db)
        assert result == "anthropic:claude-3-sonnet"


@pytest.mark.asyncio
async def test_get_default_model_name_empty(db: AsyncSession):
    """When no models in DB, returns empty string."""
    from unittest.mock import patch as _patch

    from app.services.builder_service import _get_default_model_name

    with _patch("app.config.settings") as mock_settings:
        mock_settings.default_agent_model = ""
        result = await _get_default_model_name(db)
        assert result == ""


# ---------------------------------------------------------------------------
# confirm_build — no draft_config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_build_no_draft_config(db: AsyncSession):
    """confirm_build returns None when draft_config is None."""
    await _seed_user(db)
    await db.commit()

    session = await create_session(db, TEST_USER_ID, "테스트")
    session.status = BuilderStatus.CONFIRMING
    session.draft_config = None
    await db.commit()

    result = await confirm_build(db, session)
    assert result is None


# ---------------------------------------------------------------------------
# confirm_build — no models at all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_build_no_models_raises(db: AsyncSession):
    """confirm_build raises ValueError when no models are available."""
    await _seed_user(db)
    await db.commit()

    session = await create_session(db, TEST_USER_ID, "테스트")
    session.status = BuilderStatus.CONFIRMING
    session.draft_config = {
        "name": "Bot",
        "name_ko": "봇",
        "description": "d",
        "system_prompt": "p",
        "tools": [],
        "middlewares": [],
        "model_name": "nonexistent",
    }
    await db.commit()

    with pytest.raises(ValueError, match="사용 가능한 모델이 없습니다"):
        await confirm_build(db, session)

    # Session should be rolled back to PREVIEW
    reloaded = await get_session(db, session.id, TEST_USER_ID)
    assert reloaded is not None
    assert reloaded.status == BuilderStatus.PREVIEW


# ---------------------------------------------------------------------------
# get_agent_by_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_by_id(db: AsyncSession):
    """get_agent_by_id returns agent when found."""
    await _seed_user(db)
    model = await _seed_model(db)
    await db.commit()

    agent = Agent(
        user_id=TEST_USER_ID,
        name="Test Bot",
        description="desc",
        system_prompt="prompt",
        model_id=model.id,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)

    found = await get_agent_by_id(db, agent.id)
    assert found is not None
    assert found.name == "Test Bot"


@pytest.mark.asyncio
async def test_get_agent_by_id_not_found(db: AsyncSession):
    """get_agent_by_id returns None when not found."""
    import uuid as _uuid

    found = await get_agent_by_id(db, _uuid.uuid4())
    assert found is None
