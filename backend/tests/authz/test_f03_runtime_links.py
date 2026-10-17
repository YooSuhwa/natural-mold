"""F-03 runtime half (plan 10.6 "끊긴 연결은 실행 시 제외되거나 실행을 막습니다", 12.2).

Run preparation never binds a broken link (optional ones are dropped, even when the
agent was loaded before the view), a required broken link in the one-hop tree refuses
the run with ``409 agent_not_ready``, editors still see broken links, and an editor
must name a kept broken link in ``reattach_ids`` to re-attach it (never implicit).
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.broken_links import require_runnable_links, use_runtime_link_view
from app.exceptions import AppError
from app.models.agent_subagent import AgentSubAgentLink
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink
from app.services import chat_service
from app.services.runtime_links import runnable_agent
from tests.authz.d04_support import OTHER_USER_ID, add_tool
from tests.authz.e01_support import add_agent, add_skill, seed
from tests.authz.f03_support import delegation_state, link_state
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession

CTX = unit_context(TEST_USER_ID)
BROKEN = {"link_status": "broken", "broken_reason": "delegator_lost_access"}


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _agent_with(db: AsyncSession, *, required: bool):
    agent = await add_agent(db)
    good, bad = await add_tool(db, name="good"), await add_tool(db, name="bad")
    db.add(AgentToolLink(agent_id=agent.id, tool_id=good.id, delegated_by=TEST_USER_ID))
    db.add(
        AgentToolLink(
            agent_id=agent.id,
            tool_id=bad.id,
            delegated_by=TEST_USER_ID,
            required=required,
            **BROKEN,
        )
    )
    await db.commit()
    return agent, good, bad


async def test_runtime_view_drops_optional_broken_links_editors_still_see_them(
    db: AsyncSession,
) -> None:
    agent, good, _bad = await _agent_with(db, required=False)
    async with TestSession() as edit:
        loaded = await chat_service.get_agent_with_tools(edit, agent.id, CTX)
        assert loaded is not None and len(loaded.tool_links) == 2  # edit/read paths
    async with TestSession() as run:
        loaded = await chat_service.get_agent_with_tools(run, agent.id, CTX)
        assert loaded is not None
        ready = await runnable_agent(run, loaded, CTX)  # loaded before the view: reloaded
        assert [link.tool_id for link in ready.tool_links] == [good.id]
        await require_runnable_links(run, agent.id)  # optional: the run goes on
    async with TestSession() as run:
        use_runtime_link_view(run)
        loaded = await chat_service.get_agent_with_tools(run, agent.id, CTX)
        assert loaded is not None and [link.tool_id for link in loaded.tool_links] == [good.id]


async def test_required_broken_link_refuses_the_run(db: AsyncSession) -> None:
    agent, _good, bad = await _agent_with(db, required=True)
    with pytest.raises(AppError) as refused:
        await require_runnable_links(db, agent.id)
    assert (refused.value.code, refused.value.status) == ("agent_not_ready", 409)
    assert refused.value.details["items"][0]["id"] == str(bad.id)  # type: ignore[index]


async def test_children_count_only_through_active_subagent_links(db: AsyncSession) -> None:
    parent, child = await add_agent(db), await add_agent(db)
    skill = await add_skill(db, owner=TEST_USER_ID, slug="s")
    db.add(AgentSkillLink(agent_id=child.id, skill_id=skill.id, required=True, **BROKEN))
    link = AgentSubAgentLink(parent_agent_id=parent.id, sub_agent_id=child.id, required=False)
    db.add(link)
    await db.commit()
    with pytest.raises(AppError):
        await require_runnable_links(db, parent.id)  # the child cannot run its skill
    link.link_status = "broken"  # the optional child itself is dropped instead
    await db.commit()
    await require_runnable_links(db, parent.id)


async def test_kept_broken_link_stays_broken_and_does_not_block_the_save(
    client: AsyncClient, db: AsyncSession
) -> None:
    """No implicit heal: a save that keeps a broken link leaves it broken, without
    re-validating it (the editor may have lost the object too)."""
    agent = await add_agent(db)
    lost = await add_tool(db, owner=OTHER_USER_ID, name="lost")  # not usable by me
    db.add(AgentToolLink(agent_id=agent.id, tool_id=lost.id, delegated_by=OTHER_USER_ID, **BROKEN))
    await db.commit()
    body = {"name": "renamed", "tool_ids": [str(lost.id)]}
    response = await client.put(f"/api/agents/{agent.id}", json=body)
    assert response.status_code == 200, response.text
    assert await link_state(agent.id, lost.id) == ("broken", "delegator_lost_access")
    assert await delegation_state(agent.id, "tool", lost.id) == []
    body["reattach_ids"] = [str(lost.id)]  # explicit re-attach runs the attach checks
    assert (await client.put(f"/api/agents/{agent.id}", json=body)).status_code == 400


async def test_link_writers_refuse_a_run_prep_session(db: AsyncSession) -> None:
    from app.schemas.agent import AgentUpdate
    from app.services.agent_update_service import update_agent

    agent = await add_agent(db)
    use_runtime_link_view(db)
    with pytest.raises(RuntimeError):
        await update_agent(db, agent, AgentUpdate(tool_ids=[]), context=CTX)
