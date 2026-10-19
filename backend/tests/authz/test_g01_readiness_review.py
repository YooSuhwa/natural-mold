"""G-01 review fixes: per-agent visibility, effective required, summary cache and limits,
narrow invalidation, operator-managed system credentials (plan 12.1-12.3)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime
from app.authz.runtime import legacy_service
from app.authz.service import AuthzService
from app.models.agent_subagent import AgentSubAgentLink
from app.models.credential import Credential
from app.models.mcp_server import McpServer
from app.models.mcp_tool import AgentMcpToolLink
from app.models.tool import Tool
from app.rate_limit import limiter
from app.routers.agent_readiness import readiness_summary
from app.services import readiness_cache, readiness_service
from tests.authz.authz_service_testkit import RecordingClient
from tests.authz.d04_support import add_server, add_tool
from tests.authz.e01_support import THIRD_USER_ID, add_agent, add_member, seed
from tests.authz.f05_support import CALLER, OWNER, shared_agent
from tests.authz.g01_support import assess, enforce, link_tool, results, runnable
from tests.conftest import TestSession


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> AsyncIterator[None]:
    await seed(db)
    await add_member(db, THIRD_USER_ID)
    readiness_cache.invalidate_all()
    yield
    readiness_cache.invalidate_all()


async def test_each_item_is_shown_by_its_own_agents_visibility(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = await add_agent(db, owner=CALLER)
    mine = await link_tool(db, root, await add_tool(db, owner=CALLER, name="mine"))
    child = await add_agent(db, owner=OWNER)
    hidden = await link_tool(db, child, await add_tool(db, owner=OWNER, name="hidden"))
    db.add(AgentSubAgentLink(parent_agent_id=root.id, sub_agent_id=child.id))
    await db.commit()
    authz = enforce(
        monkeypatch,
        (f"user:{CALLER}", "can_use", f"llm_model:{root.model_id}"),
        (f"agent:{root.id}", "can_run", f"agent:{child.id}"),
        (f"user:{OWNER}", "can_use", f"llm_model:{child.model_id}"),
    )
    readiness = await assess(db, root, authz)
    assert readiness.visible == frozenset({root.id})  # I may run the child, not view it
    body = readiness_service.present(readiness)
    shown = {item.get("id") for item in body["items"]}
    assert str(mine.id) in shown and str(child.id) in shown
    assert str(hidden.id) not in str(body) and "hidden" not in str(body)
    assert {"kind": "tool", "count": 1, "required": False, "result": "ready"} in body["items"]


async def test_readiness_uses_the_planners_effective_required(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await shared_agent(db, mode="fixed")
    lent = await link_tool(db, agent, await add_tool(db, owner=THIRD_USER_ID))  # optional
    client = RecordingClient(allowed=frozenset(runnable(agent)))
    types = frozenset({"agent", "llm_model"})  # tools not enforced yet: the run is refused
    authz = AuthzService(client, mode="enforce", enforce_types=types)  # type: ignore[arg-type]
    monkeypatch.setattr(runtime, "_current", authz)
    readiness = await assess(db, agent, authz)
    assert results(readiness)[("tool", lent.id)] == ("no_permission", True)
    assert readiness.overall == "blocked"


async def test_inactive_system_credential_is_unavailable(db: AsyncSession) -> None:
    agent = await add_agent(db, owner=CALLER)
    credential = Credential(
        user_id=None,
        is_system=True,
        scope="platform",
        definition_key="http_bearer",
        name="ops",
        data_encrypted="x",
        key_id="k1",
        status="auth_needed",
    )
    db.add(credential)
    await db.flush()
    tool = Tool(
        is_system=True,
        definition_key="http_request",
        name="sys",
        parameters={},
        credential_id=credential.id,
    )
    db.add(tool)
    await db.commit()
    await link_tool(db, agent, tool)
    readiness = await assess(db, agent, legacy_service())
    item = next(i for i in readiness.items if i.kind == "tool")
    assert (item.result, item.credential, item.action) == ("unavailable", None, None)


async def test_summary_caches_hidden_agents_and_skips_visibility_checks(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    hidden = await shared_agent(db, mode="fixed")
    calls: list[object] = []
    real = readiness_service.assess_agent

    async def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return await real(*args, **kwargs)

    async def no_perms(*_args: Any) -> Any:
        raise AssertionError("summary must not ask can_view_config")

    monkeypatch.setattr(readiness_service, "assess_agent", counted)
    monkeypatch.setattr(readiness_service, "agents_permissions", no_perms)
    mine = await add_agent(db, owner=CALLER)
    body = {"agent_ids": [str(hidden.id), str(mine.id)]}
    for _ in range(2):
        response = await client.post("/api/agents/readiness-summary", json=body)
        assert response.status_code == 200, response.text
        assert list(response.json()) == [str(mine.id)]
    assert calls == [{"with_visibility": False}] * 2  # second round: both cached


def test_summary_is_rate_limited_per_caller() -> None:
    name = f"{readiness_summary.__module__}.{readiness_summary.__name__}"
    assert name in limiter._route_limits  # noqa: SLF001 - slowapi registry


async def test_status_only_columns_do_not_flush_the_cache(db: AsyncSession) -> None:
    agent = await add_agent(db, owner=CALLER)
    server, mcp_tool = await add_server(db, owner=CALLER)
    db.add(AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=mcp_tool.id))
    await db.commit()
    key = (None, None, CALLER, agent.id)

    def cached() -> bool:
        return readiness_cache.get(key) is not None

    readiness_cache.put(key, {"overall": "ready", "counts": {}})
    async with TestSession() as other:
        row = await other.get(McpServer, server.id)
        assert row is not None
        row.health_status, row.last_pinged_at = "error", None
        await other.commit()
        await other.execute(
            update(McpServer).where(McpServer.id == server.id).values(health_message="x")
        )
        await other.commit()
    assert cached()
    async with TestSession() as other:
        await other.execute(
            update(McpServer).where(McpServer.id == server.id).values(status="unreachable")
        )
        await other.commit()
    assert not cached()
    readiness_cache.put(key, {"overall": "ready", "counts": {}})
    async with TestSession() as other:
        row = await other.get(McpServer, server.id)
        assert row is not None
        row.status = "connected"
        await other.commit()
    assert not cached()
