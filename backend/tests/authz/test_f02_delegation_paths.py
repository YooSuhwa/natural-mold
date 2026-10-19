"""F-02 (plan 10.6): every link path reconciles delegation grants through one helper.

Assistant link tools, builder confirm, the blueprint model credential and the
marketplace MCP snapshot refresh call ``sync_agent_delegations``; ``commit_and_apply``
hands the queued outbox rows to OpenFGA after commit. The D17 list reads the grants
batched (E-06 note 6) and lists the model credential once.
"""

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.assistant.tools.write_tools import build_write_tools
from app.authz import agent_delegations
from app.authz.agent_delegations import commit_and_apply, sync_agent_delegations
from app.authz.delegation_accounts import delegated_accounts, delegation_carriers
from app.authz.resource_lifecycle import LifecycleChange
from app.marketplace.install.bindings import _materialize_mcp_tool_snapshot
from app.models.agent import Agent
from app.models.authz import ResourceGrant
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.tool import AgentToolLink
from app.schemas.builder import BuilderStatus
from app.services.builder_service import confirm_build, create_session
from tests.authz.d04_support import OTHER_ORG_ID, add_server, add_tool
from tests.authz.e01_support import add_agent, add_credential, add_skill, seed
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession

CTX = unit_context(TEST_USER_ID)
FACTORY = "app.agent_runtime.assistant.tools.write_tools.async_session_factory"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _objects(agent_id: uuid.UUID) -> set[tuple[str, uuid.UUID]]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(ResourceGrant).where(
                ResourceGrant.principal_id == agent_id,
                ResourceGrant.grant_kind == "delegation",
                ResourceGrant.state == "active",
            )
        )
        return {(g.resource_type, g.resource_id) for g in rows}


def _tool(agent: Agent, name: str):  # noqa: ANN202
    tools = build_write_tools(None, agent.id, CTX)  # type: ignore[arg-type]
    return next(t for t in tools if t.name == name)


async def test_assistant_link_tools_grant_and_revoke(db: AsyncSession) -> None:
    agent, tool = await add_agent(db), await add_tool(db, name="lookup")
    skill = await add_skill(db, owner=TEST_USER_ID, slug="guide")
    _, mcp = await add_server(db, name="srv")
    with patch(FACTORY, TestSession):
        await _tool(agent, "add_tool_to_agent").ainvoke({"tool_names": ["lookup"]})
        await _tool(agent, "add_mcp_tool_to_agent").ainvoke({"mcp_tool_names": [mcp.name]})
        await _tool(agent, "add_skill_to_agent").ainvoke({"skill_names": ["guide"]})
        assert await _objects(agent.id) == {
            ("tool", tool.id),
            ("mcp_server", mcp.server_id),
            ("skill", skill.id),
        }
        await _tool(agent, "remove_tool_from_agent").ainvoke({"tool_names": ["lookup"]})
        await _tool(agent, "remove_mcp_tool_from_agent").ainvoke({"mcp_tool_names": [mcp.name]})
        await _tool(agent, "remove_skill_from_agent").ainvoke({"skill_names": ["guide"]})
    assert await _objects(agent.id) == set()


async def test_assistant_subagent_add_and_remove(db: AsyncSession) -> None:
    agent, sub = await add_agent(db), await add_agent(db)
    with patch(FACTORY, TestSession):
        await _tool(agent, "add_subagent_to_agent").ainvoke({"agent_ids": [str(sub.id)]})
        assert await _objects(agent.id) == {("agent", sub.id)}
        await _tool(agent, "remove_subagent_from_agent").ainvoke({"agent_ids": [str(sub.id)]})
    assert await _objects(agent.id) == set()


async def test_builder_confirm_records_delegations(db: AsyncSession) -> None:
    tool = await add_tool(db, name="Builder Tool")
    await add_agent(db)  # seeds a model for the builder
    session = await create_session(db, CTX, "봇")
    session.status = BuilderStatus.CONFIRMING
    session.draft_config = {"name": "B", "system_prompt": "p", "tools": ["Builder Tool"]}
    await db.commit()
    agent = await confirm_build(db, session, context=CTX)
    assert agent is not None
    assert await _objects(agent.id) == {("tool", tool.id)}


async def test_model_credential_grant_follows_llm_credential_id(db: AsyncSession) -> None:
    agent, first, second = await add_agent(db), await add_credential(db), await add_credential(db)
    agent.llm_credential_id = first.id
    await sync_agent_delegations(db, CTX, agent.id, added={("credential", first.id)})
    await db.commit()
    assert await _objects(agent.id) == {("credential", first.id)}
    agent.llm_credential_id = second.id
    await sync_agent_delegations(db, CTX, agent.id, added={("credential", second.id)})
    await db.commit()
    assert await _objects(agent.id) == {("credential", second.id)}  # first one revoked
    agent.llm_credential_id = None
    await sync_agent_delegations(db, CTX, agent.id)
    await db.commit()
    assert await _objects(agent.id) == set()


async def test_only_added_objects_are_granted_and_foreign_grants_are_skipped(
    db: AsyncSession,
) -> None:
    agent, kept, new = await add_agent(db), await add_tool(db), await add_tool(db, name="n")
    db.add_all(
        [
            AgentToolLink(agent_id=agent.id, tool_id=kept.id),
            AgentToolLink(agent_id=agent.id, tool_id=new.id),
        ]
    )
    await sync_agent_delegations(db, CTX, agent.id, added={("tool", new.id)})
    await db.commit()
    assert await _objects(agent.id) == {("tool", new.id)}  # the kept link: F-03 reports it
    foreign = ResourceGrant(
        org_id=OTHER_ORG_ID,
        tenant_id=TEST_TENANT_ID,
        resource_type="skill",
        resource_id=uuid.uuid4(),
        relation="consumer",
        principal_type="agent",
        principal_id=agent.id,
        grant_kind="delegation",
        source_link={"link": "agent_skills"},
    )
    db.add(foreign)
    await db.commit()
    await sync_agent_delegations(db, CTX, agent.id)  # unlinked but foreign: left alone
    await db.commit()
    assert ("skill", foreign.resource_id) in await _objects(agent.id)


async def test_snapshot_refresh_revokes_the_server_of_a_pruned_last_tool(
    db: AsyncSession,
) -> None:
    agent = await add_agent(db)
    server, gone = await add_server(db, name="srv")
    db.add(AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=gone.id))
    await sync_agent_delegations(db, CTX, agent.id, added={("mcp_server", server.id)})
    await db.commit()
    assert await _objects(agent.id) == {("mcp_server", server.id)}
    payload = {"tool_snapshot": [{"name": "renamed", "input_schema": {}}]}
    await _materialize_mcp_tool_snapshot(db, server=server, payload=payload, context=CTX)
    await db.commit()
    assert await _objects(agent.id) == set()
    assert await db.scalar(select(McpTool.id).where(McpTool.id == gone.id)) is None


async def test_commit_and_apply_hands_queued_rows_to_the_writer(db: AsyncSession) -> None:
    agent, tool = await add_agent(db), await add_tool(db)
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    change = await sync_agent_delegations(db, CTX, agent.id, added={("tool", tool.id)})
    assert change.outbox_ids
    applied: list[LifecycleChange] = []

    async def fake_apply(given: LifecycleChange, _authz: object) -> None:
        applied.append(given)

    extra = LifecycleChange((uuid.uuid4(),))
    with patch.object(agent_delegations, "apply_lifecycle", fake_apply):
        await commit_and_apply(db, object(), extra)  # type: ignore[arg-type]
        await commit_and_apply(db, object())  # type: ignore[arg-type]
    assert applied[0].outbox_ids == (*extra.outbox_ids, *change.outbox_ids)
    assert applied[1].outbox_ids == ()  # the queue is cleared once handed over


async def test_d17_list_reads_grants_batched_and_lists_the_model_credential_once(
    db: AsyncSession,
) -> None:
    agent, credential = await add_agent(db), await add_credential(db)
    tool = await add_tool(db)
    tool.credential_id = credential.id
    agent.llm_credential_id = credential.id
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    added = {("tool", tool.id), ("credential", credential.id)}
    await sync_agent_delegations(db, CTX, agent.id, added=added)
    await db.commit()
    assert set(await delegation_carriers(db, agent.id)) == {
        ("tool", tool.id, credential.id),
        ("credential", credential.id, credential.id),
    }
    items = await delegated_accounts(db, agent.id)
    assert sorted((i["resource_type"], i["resource_id"]) for i in items) == [
        ("agent", str(agent.id)),
        ("tool", str(tool.id)),
    ]


async def test_blueprint_create_agent_delegates_the_bound_model_credential(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent, credential = await add_agent(db), await add_credential(db)

    async def bound(session: AsyncSession, **_: object) -> Agent:
        found = await session.get(Agent, agent.id)
        assert found is not None
        found.llm_credential_id = credential.id  # what the blueprint's ``llm`` binding does
        await session.flush()
        return found

    target = "app.routers.agent_blueprints.agent_blueprint_service.create_agent_from_blueprint"
    with patch(target, bound):
        response = await client.post(f"/api/agent-blueprints/{uuid.uuid4()}/create-agent", json={})
    assert response.status_code == 201, response.text
    assert await _objects(agent.id) == {("credential", credential.id)}
    async with TestSession() as check:
        stored = await check.get(Agent, agent.id)
        assert stored is not None and stored.llm_credential_delegated_by == TEST_USER_ID
