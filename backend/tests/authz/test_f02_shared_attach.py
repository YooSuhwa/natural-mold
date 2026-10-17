"""F-02 (plan 9.1 PUT/POST rows, 10.6): attach shared resources the actor may use.

Enforced types accept non-owned candidates (shared to me, my groups or my org) that
OpenFGA confirms with ``can_use`` (tools, skills), ``can_invoke`` (MCP tools) or
``can_run`` (subagents); the delegation grant then names the actor as delegator.
Legacy keeps the owner-only validation (400).
"""

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.attach_candidates import shared_attachable
from app.authz.fake import FakeAuthz
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.models.authz import ResourceGrant
from app.models.tool import AgentToolLink
from tests.authz.d04_support import OTHER_USER_ID, add_server, add_tool, grant_to_me
from tests.authz.e01_support import ME, add_agent, add_skill, seed
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession

CTX = unit_context(TEST_USER_ID)


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


def _enforce(
    monkeypatch: pytest.MonkeyPatch, app: FastAPI | None, *allowed: tuple[str, str]
) -> None:
    tuples = frozenset((ME, permission, obj) for permission, obj in allowed)
    types = frozenset({"tool", "skill", "mcp_tool", "agent"})
    service = AuthzService(FakeAuthz(allowed=tuples), mode="enforce", enforce_types=types)
    monkeypatch.setattr("app.authz.runtime._current", service)
    if app is not None:  # the route itself stays legacy for the agent (owner) check

        async def legacy() -> AuthzService:
            return AuthzService(FakeAuthz(), mode="legacy")

        app.dependency_overrides[get_authz] = legacy


async def test_legacy_keeps_refusing_a_shared_tool(client: AsyncClient, db: AsyncSession) -> None:
    agent, tool = await add_agent(db), await add_tool(db, owner=OTHER_USER_ID)
    await grant_to_me(db, "tool", tool.id)
    response = await client.put(f"/api/agents/{agent.id}", json={"tool_ids": [str(tool.id)]})
    assert response.status_code == 400, response.text


async def test_enforced_shared_tool_attaches_with_can_use(
    client: AsyncClient, db: AsyncSession, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, tool = await add_agent(db), await add_tool(db, owner=OTHER_USER_ID)
    await grant_to_me(db, "tool", tool.id)
    path, body = f"/api/agents/{agent.id}", {"tool_ids": [str(tool.id)]}
    try:
        _enforce(monkeypatch, test_app)  # no can_use: still refused
        assert (await client.put(path, json=body)).status_code == 400
        _enforce(monkeypatch, test_app, ("can_use", f"tool:{tool.id}"))
        response = await client.put(path, json=body)
    finally:
        test_app.dependency_overrides.pop(get_authz, None)
    assert response.status_code == 200, response.text
    async with TestSession() as check:
        link = await check.get(AgentToolLink, (agent.id, tool.id))
        grant = await check.scalar(
            select(ResourceGrant).where(
                ResourceGrant.principal_id == agent.id, ResourceGrant.resource_id == tool.id
            )
        )
    assert link is not None and link.delegated_by == TEST_USER_ID
    assert grant is not None
    assert (grant.grant_kind, grant.granted_by) == ("delegation", TEST_USER_ID)


async def test_unshared_foreign_rows_are_never_candidates(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = await add_tool(db, owner=OTHER_USER_ID)  # no grant row: not a candidate
    _enforce(monkeypatch, None, ("can_use", f"tool:{tool.id}"))
    assert await shared_attachable(db, CTX, "tool", [tool.id]) == set()


async def test_skills_mcp_tools_and_subagents_use_their_10_6_permission(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = await add_skill(db, owner=OTHER_USER_ID, slug="shared")
    server, mcp = await add_server(db, owner=OTHER_USER_ID)
    sub = await add_agent(db, owner=OTHER_USER_ID)
    for kind, rid in (("skill", skill.id), ("mcp_server", server.id), ("agent", sub.id)):
        await grant_to_me(db, kind, rid)
    wrong = (("can_use", f"mcp_tool:{mcp.id}"), ("can_use", f"agent:{sub.id}"))
    _enforce(monkeypatch, None, ("can_use", f"skill:{skill.id}"), *wrong)
    assert await shared_attachable(db, CTX, "skill", [skill.id]) == {skill.id}
    assert await shared_attachable(db, CTX, "mcp_tool", [mcp.id]) == set()
    assert await shared_attachable(db, CTX, "agent", [sub.id]) == set()
    _enforce(
        monkeypatch, None, ("can_invoke", f"mcp_tool:{mcp.id}"), ("can_run", f"agent:{sub.id}")
    )
    assert await shared_attachable(db, CTX, "mcp_tool", [mcp.id]) == {mcp.id}
    assert await shared_attachable(db, CTX, "agent", [sub.id, uuid.uuid4()]) == {sub.id}
