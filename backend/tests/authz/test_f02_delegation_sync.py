"""F-02 (plan 10.6, D14, 7.2.1 "위임 연결·해제"): agent create/update write delegation grants.

Attaching writes ``delegation`` grants ``agent:A consumer R`` (granted by the actor,
MCP servers once per server with ``allowed_run_source``) and stamps the link's
``delegated_by``; detaching revokes them unless another link still uses the object;
kept links keep their delegator, ``required`` and ``link_status``. Legacy mode (the
default here) writes the rows and outbox entries; only the OpenFGA apply is a no-op.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_subagent import AgentSubAgentLink
from app.models.authz import ResourceGrant
from app.models.mcp_tool import AgentMcpToolLink, McpTool
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink
from tests.authz.d04_support import OTHER_USER_ID, add_server, add_tool
from tests.authz.e01_support import add_agent, add_skill, audit_actions, outbox_count, seed
from tests.authz.unit_scope_fixture import TEST_ORG_ID
from tests.conftest import TEST_USER_ID, TestSession
from tests.conftest import engine as test_engine

ALL_SOURCES = ["CHAT", "TRIGGER", "CHANNEL", "SUBAGENT", "API"]


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


async def _delegations(agent_id: uuid.UUID, state: str = "active") -> dict[tuple, ResourceGrant]:
    async with TestSession() as db:
        rows = await db.scalars(
            select(ResourceGrant).where(
                ResourceGrant.principal_type == "agent",
                ResourceGrant.principal_id == agent_id,
                ResourceGrant.grant_kind == "delegation",
                ResourceGrant.state == state,
            )
        )
        return {(g.resource_type, g.resource_id): g for g in rows}


async def _delegators(model: type, column: object, agent_id: uuid.UUID) -> set[object]:
    async with TestSession() as db:
        return set(await db.scalars(select(model.delegated_by).where(column == agent_id)))  # type: ignore[attr-defined]


async def test_create_writes_one_grant_per_link_and_mcp_server_once(
    client: AsyncClient, db: AsyncSession
) -> None:
    tool, skill = await add_tool(db), await add_skill(db, owner=TEST_USER_ID, slug="s1")
    sub = await add_agent(db)
    server, first = await add_server(db)
    second = McpTool(server_id=server.id, name="second", input_schema={})
    db.add(second)
    await db.commit()
    seed_agent = await add_agent(db)  # only for its model id
    body = {
        "name": "N",
        "system_prompt": "p",
        "model_id": str(seed_agent.model_id),
        "tool_ids": [str(tool.id)],
        "skill_ids": [str(skill.id)],
        "mcp_tool_ids": [str(first.id), str(second.id)],
        "sub_agent_ids": [str(sub.id)],
    }
    before = await outbox_count()
    response = await client.post("/api/agents", json=body)
    assert response.status_code == 201, response.text
    agent_id = uuid.UUID(response.json()["id"])

    grants = await _delegations(agent_id)
    assert set(grants) == {
        ("tool", tool.id),
        ("skill", skill.id),
        ("mcp_server", server.id),
        ("agent", sub.id),
    }  # four objects for five links: the two MCP tools share their server (10.6)
    assert {(g.relation, g.granted_by, g.org_id) for g in grants.values()} == {
        ("consumer", TEST_USER_ID, TEST_ORG_ID)
    }
    mcp = grants[("mcp_server", server.id)]
    assert (mcp.condition_name, mcp.condition_context) == (
        "allowed_run_source",
        {"allowed_sources": ALL_SOURCES},
    )
    assert mcp.source_link == {"link": "agent_mcp_tools"}
    assert await outbox_count() > before  # tuples queued in every mode (applied after commit)
    assert await _delegators(AgentMcpToolLink, AgentMcpToolLink.agent_id, agent_id) == {
        TEST_USER_ID
    }
    assert await _delegators(AgentSkillLink, AgentSkillLink.agent_id, agent_id) == {TEST_USER_ID}
    assert "delegation.create" in await audit_actions(tool.id)


async def test_update_grants_new_links_revokes_removed_and_keeps_shared_servers(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent, keep, drop = await add_agent(db), await add_tool(db), await add_tool(db, name="d")
    server, first = await add_server(db)
    second = McpTool(server_id=server.id, name="second", input_schema={})
    db.add(second)
    await db.commit()
    path = f"/api/agents/{agent.id}"
    body = {
        "tool_ids": [str(keep.id), str(drop.id)],
        "mcp_tool_ids": [str(first.id), str(second.id)],
    }
    assert (await client.put(path, json=body)).status_code == 200
    kept = (await _delegations(agent.id))[("tool", keep.id)]

    body = {"tool_ids": [str(keep.id)], "mcp_tool_ids": [str(first.id)]}
    assert (await client.put(path, json=body)).status_code == 200
    grants = await _delegations(agent.id)
    assert set(grants) == {("tool", keep.id), ("mcp_server", server.id)}  # server still used
    assert grants[("tool", keep.id)].id == kept.id  # unchanged, not re-granted
    closed = {
        **await _delegations(agent.id, "revoking"),
        **await _delegations(agent.id, "revoked"),
    }
    assert set(closed) == {("tool", drop.id)}
    assert "delegation.revoke" in await audit_actions(drop.id)

    assert (await client.put(path, json={"mcp_tool_ids": []})).status_code == 200
    assert set(await _delegations(agent.id)) == {("tool", keep.id)}  # last server tool gone


async def test_kept_links_keep_delegator_required_and_status(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent, sub, other = await add_agent(db), await add_agent(db), await add_agent(db)
    tool = await add_tool(db)
    db.add(
        AgentToolLink(
            agent_id=agent.id,
            tool_id=tool.id,
            delegated_by=OTHER_USER_ID,
            required=True,
            link_status="broken",
            broken_reason="delegator_lost_access",
        )
    )
    db.add(AgentSubAgentLink(parent_agent_id=agent.id, sub_agent_id=sub.id))
    await db.commit()
    body = {"tool_ids": [str(tool.id)], "sub_agent_ids": [str(other.id), str(sub.id)]}
    response = await client.put(f"/api/agents/{agent.id}", json=body)
    assert response.status_code == 200, response.text
    async with TestSession() as check:
        link = await check.get(AgentToolLink, (agent.id, tool.id))
        assert link is not None
        assert (link.delegated_by, link.required, link.link_status, link.broken_reason) == (
            OTHER_USER_ID,
            True,
            "broken",
            "delegator_lost_access",
        )
        positions = dict(
            (
                await check.execute(
                    select(AgentSubAgentLink.sub_agent_id, AgentSubAgentLink.position).where(
                        AgentSubAgentLink.parent_agent_id == agent.id
                    )
                )
            )
            .tuples()
            .all()
        )
    assert positions == {other.id: 0, sub.id: 1}
    # only the row created by this save is stamped and granted; links made before F-02
    # without a grant are left for the F-03 validator to report
    assert await _delegators(AgentSubAgentLink, AgentSubAgentLink.parent_agent_id, agent.id) == {
        None,
        TEST_USER_ID,
    }
    assert set(await _delegations(agent.id)) == {("agent", other.id)}


async def test_the_org_row_is_locked_before_any_link_agent_or_grant_write(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent, tool = await add_agent(db), await add_tool(db)
    statements: list[str] = []

    def record(_conn, _cursor, statement, *_args) -> None:  # noqa: ANN001
        statements.append(" ".join(statement.split()))

    engine = test_engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        body = {"name": "renamed", "tool_ids": [str(tool.id)]}
        response = await client.put(f"/api/agents/{agent.id}", json=body)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200, response.text
    lock = next(i for i, s in enumerate(statements) if s.startswith("SELECT organizations.id"))
    writes = [
        i
        for i, s in enumerate(statements)
        if s.startswith(("UPDATE agents", "INSERT INTO agent_tools", "INSERT INTO resource_grants"))
    ]
    assert writes and lock < min(writes), statements


async def test_archiving_a_subagent_revokes_its_parents_delegation(
    client: AsyncClient, db: AsyncSession
) -> None:
    parent, child = await add_agent(db), await add_agent(db)
    path = f"/api/agents/{parent.id}"
    assert (await client.put(path, json={"sub_agent_ids": [str(child.id)]})).status_code == 200
    assert set(await _delegations(parent.id)) == {("agent", child.id)}
    assert (await client.delete(f"/api/agents/{child.id}")).status_code == 204
    assert await _delegations(parent.id) == {}
    closed = {
        **await _delegations(parent.id, "revoking"),
        **await _delegations(parent.id, "revoked"),
    }
    assert set(closed) == {("agent", child.id)}


async def test_an_update_without_link_lists_writes_no_delegation(
    client: AsyncClient, db: AsyncSession
) -> None:
    agent, tool = await add_agent(db), await add_tool(db)
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id))
    await db.commit()
    response = await client.put(f"/api/agents/{agent.id}", json={"name": "renamed"})
    assert response.status_code == 200, response.text
    assert await _delegations(agent.id) == {}
