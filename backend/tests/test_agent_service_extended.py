"""Extended tests for app.services.agent_service — update with tool_configs, template, skills."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import RuntimePolicyV1, TodoPolicyV1
from app.models.agent import Agent
from app.models.model import Model
from app.models.skill import Skill
from app.models.template import Template
from app.models.tool import Tool
from app.models.user import User
from app.services.agent_service import create_agent, update_agent
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_all(db: AsyncSession) -> tuple[Model, Tool, Skill, Template]:
    """Seed user, model, tool, skill, and template."""
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)

    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o", is_default=True)
    db.add(model)

    tool = Tool(
        name="Web Search",
        definition_key="builtin:web_search",
        description="Search the web",
    )
    db.add(tool)

    skill = Skill(
        name="test_skill",
        slug="test-skill",
        user_id=TEST_USER_ID,
        description="A test skill",
        kind="text",
        storage_path="/tmp/skills/test/SKILL.md",
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


@pytest.mark.asyncio
async def test_create_agent_flushes_without_committing(db: AsyncSession):
    """Service mutation stays rollbackable so router audit can share one transaction."""
    from app.schemas.agent import AgentCreate

    model, _, _, _ = await _seed_all(db)
    await db.commit()

    agent = await create_agent(
        db,
        AgentCreate(
            name="Rollback Agent",
            system_prompt="test",
            model_id=model.id,
        ),
        TEST_USER_ID,
    )
    agent_id = agent.id
    await db.rollback()

    async with TestSession() as check_db:
        persisted = (
            await check_db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one_or_none()
    assert persisted is None


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


@pytest.mark.asyncio
async def test_create_agent_persists_omitted_null_and_explicit_runtime_policy(
    db: AsyncSession,
) -> None:
    # Given the same model and three create payload policy states.
    from app.schemas.agent import AgentCreate

    model, _, _, _ = await _seed_all(db)
    await db.commit()

    # When agents are created with omitted, explicit NULL, and explicit v1 policy.
    omitted = await create_agent(
        db,
        AgentCreate(name="Omitted", system_prompt="test", model_id=model.id),
        TEST_USER_ID,
    )
    explicit_null = await create_agent(
        db,
        AgentCreate(
            name="Null",
            system_prompt="test",
            model_id=model.id,
            runtime_policy=None,
        ),
        TEST_USER_ID,
    )
    explicit = await create_agent(
        db,
        AgentCreate(
            name="Explicit",
            system_prompt="test",
            model_id=model.id,
            runtime_policy=RuntimePolicyV1(
                version=1,
                todo=TodoPolicyV1(enabled=False),
            ),
        ),
        TEST_USER_ID,
    )

    # Then NULL remains legacy-compatible and explicit false is stored canonically.
    assert omitted.runtime_policy is None
    assert explicit_null.runtime_policy is None
    assert explicit.runtime_policy == {
        "version": 1,
        "filesystem": {"mode": "artifact_write"},
        "todo": {"enabled": False},
        "summarization": {"mode": "auto"},
    }


@pytest.mark.asyncio
async def test_update_agent_distinguishes_omitted_policy_from_explicit_null(
    db: AsyncSession,
) -> None:
    # Given an agent with an explicit false-bearing v1 policy.
    from app.schemas.agent import AgentCreate, AgentUpdate

    model, _, _, _ = await _seed_all(db)
    await db.commit()
    agent = await create_agent(
        db,
        AgentCreate(
            name="Agent",
            system_prompt="test",
            model_id=model.id,
            runtime_policy=RuntimePolicyV1(
                version=1,
                todo=TodoPolicyV1(enabled=False),
            ),
        ),
        TEST_USER_ID,
    )
    original = dict(agent.runtime_policy or {})

    # When an unrelated update omits runtime_policy.
    unchanged = await update_agent(db, agent, AgentUpdate(name="Renamed"))

    # Then the stored policy is preserved exactly.
    assert unchanged.runtime_policy == original

    # When a later update explicitly sends NULL.
    cleared = await update_agent(db, agent, AgentUpdate(runtime_policy=None))

    # Then the stored override is cleared back to legacy-compatible NULL.
    assert cleared.runtime_policy is None


@pytest.mark.asyncio
async def test_agent_response_exposes_stored_effective_and_source_policy(
    db: AsyncSession,
) -> None:
    # Given one legacy-NULL agent and one explicit stored policy.
    from app.routers.agents import _agent_to_response
    from app.schemas.agent import AgentCreate

    model, _, _, _ = await _seed_all(db)
    await db.commit()
    legacy = await create_agent(
        db,
        AgentCreate(name="Legacy", system_prompt="test", model_id=model.id),
        TEST_USER_ID,
    )
    explicit = await create_agent(
        db,
        AgentCreate(
            name="Explicit",
            system_prompt="test",
            model_id=model.id,
            runtime_policy=RuntimePolicyV1(
                version=1,
                todo=TodoPolicyV1(enabled=False),
            ),
        ),
        TEST_USER_ID,
    )

    # When both rows are serialized through the public Agent response adapter.
    legacy_response = _agent_to_response(legacy)
    explicit_response = _agent_to_response(explicit)

    # Then stored/effective/source remain distinct and explicit false survives.
    assert legacy_response.runtime_policy is None
    assert legacy_response.runtime_policy_source == "legacy_compat"
    assert legacy_response.runtime_policy_effective == RuntimePolicyV1(version=1)
    assert explicit_response.runtime_policy is not None
    assert explicit_response.runtime_policy.todo.enabled is False
    assert explicit_response.runtime_policy_effective.todo.enabled is False
    assert explicit_response.runtime_policy_source == "stored"


@pytest.mark.asyncio
async def test_agent_api_roundtrips_runtime_policy_tristate_and_rejects_malformed(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    # Given an authenticated API client and an available model.
    model, _, _, _ = await _seed_all(db)
    await db.commit()

    # When an agent is created with the policy omitted.
    created = await client.post(
        "/api/agents",
        json={
            "name": "Policy API",
            "system_prompt": "test",
            "model_id": str(model.id),
        },
    )

    # Then the response distinguishes stored NULL from effective defaults.
    assert created.status_code == 201
    body = created.json()
    agent_id = body["id"]
    assert body["runtime_policy"] is None
    assert body["runtime_policy_source"] == "legacy_compat"
    assert body["runtime_policy_effective"] == {
        "version": 1,
        "filesystem": {"mode": "artifact_write"},
        "todo": {"enabled": True},
        "summarization": {"mode": "auto"},
    }

    # When explicit false is stored and an unrelated update omits the policy.
    explicit = await client.put(
        f"/api/agents/{agent_id}",
        json={"runtime_policy": {"version": 1, "todo": {"enabled": False}}},
    )
    omitted = await client.put(f"/api/agents/{agent_id}", json={"description": "kept"})
    fetched = await client.get(f"/api/agents/{agent_id}")

    # Then update and GET preserve the complete canonical policy and false value.
    assert explicit.status_code == 200
    assert omitted.status_code == 200
    assert fetched.status_code == 200
    assert omitted.json()["runtime_policy"]["todo"]["enabled"] is False
    assert omitted.json()["runtime_policy_effective"]["todo"]["enabled"] is False
    assert omitted.json()["runtime_policy_source"] == "stored"
    assert fetched.json()["runtime_policy"]["todo"]["enabled"] is False
    assert fetched.json()["runtime_policy_effective"]["todo"]["enabled"] is False
    assert fetched.json()["runtime_policy_source"] == "stored"

    # When an explicit NULL clears the stored policy.
    cleared = await client.put(f"/api/agents/{agent_id}", json={"runtime_policy": None})

    # Then the response returns to legacy provenance without changing effective defaults.
    assert cleared.status_code == 200
    assert cleared.json()["runtime_policy"] is None
    assert cleared.json()["runtime_policy_source"] == "legacy_compat"
    assert cleared.json()["runtime_policy_effective"]["todo"]["enabled"] is True

    # When malformed and unknown-version policy is submitted.
    malformed = await client.put(
        f"/api/agents/{agent_id}",
        json={"runtime_policy": {"version": 1, "todo": {"enabled": 0}}},
    )
    unknown = await client.put(
        f"/api/agents/{agent_id}",
        json={"runtime_policy": {"version": 2}},
    )

    # Then the public boundary fails closed with bounded validation responses.
    assert malformed.status_code == 422
    assert unknown.status_code == 422
