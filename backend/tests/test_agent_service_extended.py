"""Extended tests for app.services.agent_service — update with tool_configs, template, skills."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.models.skill import Skill
from app.models.template import Template
from app.models.tool import Tool
from app.models.user import User
from app.services.agent_service import create_agent, update_agent
from tests.conftest import TEST_USER_ID


async def _seed_all(db: AsyncSession) -> tuple[Model, Tool, Skill, Template]:
    """Seed user, model, tool, skill, and template."""
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)

    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o", is_default=True)
    db.add(model)

    tool = Tool(
        name="Web Search",
        type="prebuilt",
        is_system=True,
        description="Search the web",
    )
    db.add(tool)

    skill = Skill(
        name="test_skill",
        user_id=TEST_USER_ID,
        description="A test skill",
        content="skill content here",
    )
    db.add(skill)

    template = Template(
        name="Weather Bot",
        description="Weather template",
        category="utility",
        system_prompt="You are a weather bot.",
        recommended_tools=["Web Search"],
    )
    db.add(template)

    await db.flush()
    return model, tool, skill, template


# ---------------------------------------------------------------------------
# create_agent — with template auto-link tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_agent_with_template_tools(db: AsyncSession):
    """create_agent auto-links tools from template.recommended_tools."""
    from app.schemas.agent import AgentCreate

    model, tool, _, template = await _seed_all(db)
    await db.commit()

    agent = await create_agent(
        db,
        AgentCreate(
            name="Template Agent",
            system_prompt="test",
            model_id=model.id,
            template_id=template.id,
        ),
        TEST_USER_ID,
    )

    assert agent is not None
    assert len(agent.tool_links) == 1
    assert agent.tool_links[0].tool_id == tool.id


# ---------------------------------------------------------------------------
# create_agent — with skills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_agent_with_skills(db: AsyncSession):
    """create_agent with skill_ids creates AgentSkillLink records."""
    from app.schemas.agent import AgentCreate

    model, _, skill, _ = await _seed_all(db)
    await db.commit()

    agent = await create_agent(
        db,
        AgentCreate(
            name="Skill Agent",
            system_prompt="test",
            model_id=model.id,
            skill_ids=[skill.id],
        ),
        TEST_USER_ID,
    )

    assert len(agent.skill_links) == 1
    assert agent.skill_links[0].skill_id == skill.id


# ---------------------------------------------------------------------------
# update_agent — change tool_ids (replaces existing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_agent_tool_ids(db: AsyncSession):
    """update_agent with tool_ids replaces all existing tool links."""
    from app.schemas.agent import AgentCreate, AgentUpdate

    model, tool, _, _ = await _seed_all(db)
    await db.commit()

    agent = await create_agent(
        db,
        AgentCreate(
            name="Agent",
            system_prompt="test",
            model_id=model.id,
            tool_ids=[tool.id],
        ),
        TEST_USER_ID,
    )
    assert len(agent.tool_links) == 1

    # Update with empty tool_ids (remove all tools)
    updated = await update_agent(
        db,
        agent,
        AgentUpdate(tool_ids=[]),
    )
    assert len(updated.tool_links) == 0


# ---------------------------------------------------------------------------
# update_agent — update multiple fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_agent_multiple_fields(db: AsyncSession):
    """update_agent handles multiple field updates at once."""
    from app.schemas.agent import AgentCreate, AgentUpdate, MiddlewareConfigEntry

    model, _, _, _ = await _seed_all(db)
    await db.commit()

    agent = await create_agent(
        db,
        AgentCreate(
            name="Agent",
            system_prompt="original",
            model_id=model.id,
        ),
        TEST_USER_ID,
    )

    updated = await update_agent(
        db,
        agent,
        AgentUpdate(
            name="Renamed",
            description="new desc",
            system_prompt="updated prompt",
            is_favorite=True,
            model_params={"temperature": 0.5},
            middleware_configs=[MiddlewareConfigEntry(type="summarization", params={})],
        ),
    )

    assert updated.name == "Renamed"
    assert updated.description == "new desc"
    assert updated.system_prompt == "updated prompt"
    assert updated.is_favorite is True
    assert updated.model_params == {"temperature": 0.5}
    assert updated.middleware_configs is not None
    assert len(updated.middleware_configs) == 1


# ---------------------------------------------------------------------------
# update_agent — update skill_ids
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_agent_skill_ids(db: AsyncSession):
    """update_agent with skill_ids replaces existing skill links."""
    from app.schemas.agent import AgentCreate, AgentUpdate

    model, _, skill, _ = await _seed_all(db)
    await db.commit()

    agent = await create_agent(
        db,
        AgentCreate(
            name="Agent",
            system_prompt="test",
            model_id=model.id,
            skill_ids=[skill.id],
        ),
        TEST_USER_ID,
    )
    assert len(agent.skill_links) == 1

    # Remove skills
    updated = await update_agent(
        db,
        agent,
        AgentUpdate(skill_ids=[]),
    )
    assert len(updated.skill_links) == 0
