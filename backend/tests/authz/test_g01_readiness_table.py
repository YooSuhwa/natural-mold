"""G-01: the 12.1 per-item table and the 12.2 overall state (plan 12.1, 12.2).

Owner OTHER shares an agent with the caller TEST; THIRD owns resources the agent uses.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.runtime import legacy_service
from app.exceptions import AppError
from app.models.agent_subagent import AgentSubAgentLink
from app.models.mcp_tool import AgentMcpToolLink
from app.models.model import Model
from app.models.tool import Tool
from tests.authz.d04_support import add_server, add_tool
from tests.authz.e01_support import THIRD_USER_ID, add_agent, add_member, seed
from tests.authz.f05_support import (
    CALLER,
    OWNER,
    bind,
    per_user_server,
    per_user_tool,
    secret,
    shared_agent,
)
from tests.authz.g01_support import AS_CALLER, assess, enforce, link_tool, results, runnable


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)
    await add_member(db, THIRD_USER_ID)


async def test_ready_when_every_item_is_ready(db: AsyncSession) -> None:
    agent = await add_agent(db, owner=CALLER)
    tool = await link_tool(db, agent, await add_tool(db, owner=CALLER), required=True)
    readiness = await assess(db, agent, legacy_service())
    assert results(readiness) == {
        ("llm_model", agent.model_id): ("ready", True),
        ("tool", tool.id): ("ready", True),
    }
    assert readiness.overall == "ready" and readiness.can_view_config


async def test_no_permission_for_a_denied_or_broken_link(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await shared_agent(db, mode="fixed")
    denied = await link_tool(db, agent, await add_tool(db, owner=THIRD_USER_ID, name="d"))
    broken = await add_tool(db, owner=OWNER, name="b")
    await link_tool(db, agent, broken, link_status="broken", broken_reason="revoked")
    readiness = await assess(db, agent, enforce(monkeypatch, *runnable(agent)))
    found = results(readiness)
    assert found[("tool", denied.id)] == ("no_permission", False)
    assert found[("tool", broken.id)] == ("no_permission", False)
    assert readiness.overall == "limited"  # only optional items are wrong
    required = await shared_agent(db, mode="fixed")
    await link_tool(db, required, await add_tool(db, owner=THIRD_USER_ID), required=True)
    assert (await assess(db, required, enforce(monkeypatch, *runnable(required)))).overall == (
        "blocked"
    )


async def test_a_lent_link_is_ready(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = await shared_agent(db, mode="fixed")
    lent = await link_tool(db, agent, await add_tool(db, owner=THIRD_USER_ID), required=True)
    authz = enforce(
        monkeypatch, *runnable(agent), (f"agent:{agent.id}", "can_use", f"tool:{lent.id}")
    )
    readiness = await assess(db, agent, authz)
    assert results(readiness)[("tool", lent.id)] == ("ready", True)
    assert readiness.overall == "ready" and not readiness.can_view_config


async def test_connect_by_me_for_my_missing_or_expired_per_user_connection(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await shared_agent(db, mode="per_user")
    tool = await per_user_tool(db, agent)
    authz = enforce(monkeypatch, *runnable(agent), (AS_CALLER, "can_use", f"tool:{tool.id}"))
    item = next(i for i in (await assess(db, agent, authz)).items if i.kind == "tool")
    assert (item.result, item.credential, item.action) == (
        "connect_by_me",
        {"mode": "per_user", "status": "missing"},
        {"type": "connect"},
    )
    mine = await secret(db, CALLER, "v")
    await bind(db, CALLER, "tool", tool.id, mine)
    assert results(await assess(db, agent, authz))[("tool", tool.id)][0] == "ready"
    mine.status = "expired"
    await db.commit()
    item = next(i for i in (await assess(db, agent, authz)).items if i.kind == "tool")
    assert item.result == "connect_by_me"
    assert item.credential == {
        "mode": "per_user",
        "status": "expired",
        "credential_id": str(mine.id),
    }


async def test_needs_connection_when_only_my_connection_is_missing(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await shared_agent(db, mode="per_user")
    server = await per_user_server(db, agent)
    link = await db.scalar(select(AgentMcpToolLink).where(AgentMcpToolLink.agent_id == agent.id))
    assert link is not None
    link.required = True
    await db.commit()
    invoke = (AS_CALLER, "can_invoke", f"mcp_tool:{link.mcp_tool_id}")
    readiness = await assess(db, agent, enforce(monkeypatch, *runnable(agent), invoke))
    assert results(readiness)[("mcp_server", server.id)] == ("connect_by_me", True)
    assert readiness.overall == "needs_connection"


async def test_owner_must_connect_for_the_owner_credential(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await shared_agent(db, mode="fixed")
    tool = await add_tool(db, owner=OWNER)
    credential = await secret(db, OWNER, "v")
    tool.credential_id = credential.id
    credential.status = "auth_needed"
    await db.commit()
    await link_tool(db, agent, tool, required=True)
    per_user = await per_user_tool(db, agent)  # fixed: the owner's own binding is missing
    readiness = await assess(db, agent, enforce(monkeypatch, *runnable(agent)))
    found = results(readiness)
    assert found[("tool", tool.id)] == ("owner_must_connect", True)
    assert found[("tool", per_user.id)] == ("owner_must_connect", False)
    item = next(i for i in readiness.items if i.id == tool.id)
    assert item.credential == {"mode": "shared", "status": "auth_needed"}  # not my credential
    assert item.action == {"type": "notify_owner"}
    assert readiness.overall == "blocked"


async def test_unavailable_for_down_server_disabled_tool_and_hidden_model(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await add_agent(db, owner=CALLER)
    server, mcp_tool = await add_server(db, owner=CALLER)
    server.status = "unreachable"
    tool = await add_tool(db, owner=CALLER)
    tool.enabled = False
    await db.commit()
    await link_tool(db, agent, tool)
    db.add(AgentMcpToolLink(agent_id=agent.id, mcp_tool_id=mcp_tool.id))
    await db.commit()
    readiness = await assess(db, agent, legacy_service())
    found = results(readiness)
    assert found[("tool", tool.id)] == ("unavailable", False)
    assert found[("mcp_server", server.id)] == ("unavailable", False)
    assert readiness.overall == "limited"
    model = await db.get(Model, agent.model_id)
    assert model is not None
    model.is_visible = False
    await db.commit()
    readiness = await assess(db, agent, legacy_service())
    assert results(readiness)[("llm_model", agent.model_id)] == ("unavailable", True)
    assert readiness.overall == "blocked"


async def test_a_denied_model_blocks(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = await shared_agent(db, mode="fixed")
    authz = enforce(monkeypatch, (AS_CALLER, "can_run", f"agent:{agent.id}"))
    readiness = await assess(db, agent, authz)
    assert results(readiness)[("llm_model", agent.model_id)] == ("no_permission", True)
    assert readiness.overall == "blocked"


async def test_platform_rows_follow_the_catalog_policy(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await shared_agent(db, mode="fixed")
    on, off = (
        Tool(is_system=True, definition_key="http_request", name=name, parameters={})
        for name in ("on", "off")
    )
    db.add_all([on, off])
    await db.commit()
    await link_tool(db, agent, on)
    await link_tool(db, agent, off)
    authz = enforce(monkeypatch, *runnable(agent), (f"user:{OWNER}", "can_use", f"tool:{on.id}"))
    found = results(await assess(db, agent, authz))
    assert found[("tool", on.id)] == ("ready", False)
    assert found[("tool", off.id)] == ("no_permission", False)  # catalog switched off


async def test_subagent_items_follow_their_link(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await add_agent(db, owner=CALLER)
    child = await add_agent(db, owner=CALLER)
    model = await db.get(Model, child.model_id)
    assert model is not None
    model.is_visible = False
    db.add(AgentSubAgentLink(parent_agent_id=agent.id, sub_agent_id=child.id))
    await db.commit()
    readiness = await assess(db, agent, legacy_service())
    found = results(readiness)
    assert found[("sub_agent", child.id)] == ("ready", False)
    assert found[("llm_model", child.model_id)] == ("unavailable", False)  # optional link
    assert readiness.overall == "limited"
    link = await db.get(AgentSubAgentLink, (agent.id, child.id))
    assert link is not None
    link.required = True
    await db.commit()
    assert (await assess(db, agent, legacy_service())).overall == "blocked"


async def test_hidden_or_not_runnable_agents_keep_the_run_answers(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_id = (await shared_agent(db, mode="fixed")).id
    with pytest.raises(AppError) as caught:
        await assess(db, agent_id, legacy_service())
    assert caught.value.status == 404
    authz = enforce(monkeypatch, (AS_CALLER, "can_view_config", f"agent:{agent_id}"))
    with pytest.raises(AppError) as caught:
        await assess(db, agent_id, authz)
    assert caught.value.status == 404  # agent visibility starts at can_run
