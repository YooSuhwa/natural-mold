"""F-06: ``RuntimeAccessPlanner`` decisions per link (plan 11.1, 11.2).

Owner OTHER shares an agent with the caller TEST; THIRD owns resources the agent uses.
"""

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.service import AuthzService
from app.exceptions import AppError
from app.models.skill import AgentSkillLink
from app.models.tool import AgentToolLink, Tool
from app.services.authorized_runtime import prepare_authorized_runtime
from tests.authz.authz_service_testkit import RecordingClient
from tests.authz.d04_support import add_tool
from tests.authz.e01_support import THIRD_USER_ID, add_member, add_skill, seed
from tests.authz.f05_support import CALLER, OWNER, identity, loaded, shared_agent
from tests.authz.unit_scope_fixture import unit_context

AS_CALLER = f"user:{CALLER}"


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)
    await add_member(db, THIRD_USER_ID)


def _service(*allowed: tuple[str, str, str], down: bool = False) -> tuple[AuthzService, Any]:
    client = RecordingClient(allowed=frozenset(allowed), unavailable=down)
    return AuthzService(client, mode="enforce"), client  # type: ignore[arg-type]


async def _link_tool(db: AsyncSession, agent: Any, tool: Tool, **link: Any) -> Tool:
    db.add(AgentToolLink(agent_id=agent.id, tool_id=tool.id, **link))
    await db.commit()
    return tool


async def _prepare(db: AsyncSession, agent: Any, authz: AuthzService) -> Any:
    return await prepare_authorized_runtime(
        await loaded(db, agent),
        path="chat",
        context=unit_context(CALLER),
        db=db,
        identity=identity(agent),
        authz=authz,
    )


def _item(agent: Any, kind: str, *, result: str = "permission_denied", reason: str = "denied"):
    # F-03's agent_not_ready item shape, without the resource id (L2).
    return {
        "kind": kind,
        "agent_id": str(agent.id),
        "required": True,
        "result": result,
        "reason": reason,
    }


def _excluded(runtime: Any) -> set[tuple[str, UUID, str]]:
    return {(item.kind, item.resource_id, item.reason) for item in runtime.plan.excluded}


def _ids(links: tuple[Any, ...], attr: str = "tool_id") -> set[UUID]:
    return {getattr(link, attr) for link in links}


def _base(agent: Any) -> set[tuple[str, str, str]]:
    return {
        (AS_CALLER, "can_run", f"agent:{agent.id}"),
        (f"user:{agent.user_id}", "can_use", f"llm_model:{agent.model_id}"),
    }


async def test_a_fixed_shared_agent_decides_links_as_the_agent(db: AsyncSession) -> None:
    agent = await shared_agent(db, mode="fixed")
    own = await _link_tool(db, agent, await add_tool(db, owner=OWNER, name="own"))
    lent = await _link_tool(db, agent, await add_tool(db, owner=THIRD_USER_ID, name="lent"))
    denied = await _link_tool(db, agent, await add_tool(db, owner=THIRD_USER_ID, name="no"))
    broken = await add_tool(db, owner=THIRD_USER_ID, name="broken")
    await _link_tool(db, agent, broken, link_status="broken", broken_reason="revoked")
    authz, client = _service(*_base(agent), (f"agent:{agent.id}", "can_use", f"tool:{lent.id}"))
    runtime = await _prepare(db, agent, authz)
    assert _ids(runtime.plan.tool_links) == {own.id, lent.id}
    assert _excluded(runtime) == {("tool", denied.id, "denied"), ("tool", broken.id, "broken")}
    # Serialized views never carry the resource ids (a runner may not see those rows).
    assert sorted(runtime.excluded_capabilities, key=str) == [
        {"type": "tool", "reason": "broken"},
        {"type": "tool", "reason": "denied"},
    ]
    assert runtime.plan.excluded_counts == {"tool": 2}
    asked = {(q.user, q.object) for q in client.queries if q.relation == "can_use"}
    assert (f"agent:{agent.id}", f"tool:{own.id}") not in asked  # owner fast path
    assert (f"agent:{agent.id}", f"tool:{denied.id}") in asked
    assert {entry["tool_id"] for entry in runtime.tools_config} == {str(own.id), str(lent.id)}


async def test_a_caller_identity_run_decides_as_the_caller(db: AsyncSession) -> None:
    agent = await shared_agent(db, mode="per_user")
    tool = await _link_tool(db, agent, await add_tool(db, owner=OWNER, name="owner's"))
    skill = await add_skill(db, owner=THIRD_USER_ID, slug="lent-skill")
    db.add(AgentSkillLink(agent_id=agent.id, skill_id=skill.id))
    await db.commit()
    base = {(AS_CALLER, "can_run", f"agent:{agent.id}")}
    model = (AS_CALLER, "can_use", f"llm_model:{agent.model_id}")
    authz, _ = _service(*base, model, (AS_CALLER, "can_use", f"tool:{tool.id}"))
    runtime = await _prepare(db, agent, authz)
    assert _ids(runtime.plan.tool_links) == {tool.id}
    assert _excluded(runtime) == {("skill", skill.id, "denied")}
    assert runtime.agent_skills == []


async def test_a_denied_required_link_or_model_is_agent_not_ready(db: AsyncSession) -> None:
    agent = await shared_agent(db, mode="fixed")
    await _link_tool(db, agent, await add_tool(db, owner=THIRD_USER_ID), required=True)
    authz, _ = _service(*_base(agent))
    with pytest.raises(AppError) as caught:
        await _prepare(db, agent, authz)
    assert (caught.value.status, caught.value.code) == (409, "agent_not_ready")
    assert caught.value.details == {"overall": "blocked", "items": [_item(agent, "tool")]}
    no_model, _ = _service((AS_CALLER, "can_run", f"agent:{agent.id}"))
    with pytest.raises(AppError) as caught:
        await _prepare(db, agent, no_model)
    assert caught.value.code == "agent_not_ready"


async def test_platform_rows_follow_the_catalog_as_the_run_account(db: AsyncSession) -> None:
    agent = await shared_agent(db, mode="fixed")
    on, off = (
        Tool(is_system=True, definition_key="http_request", name=name, parameters={})
        for name in ("on", "off")
    )
    db.add_all([on, off])
    await db.commit()
    await _link_tool(db, agent, on)
    await _link_tool(db, agent, off)
    authz, client = _service(*_base(agent), (f"user:{OWNER}", "can_use", f"tool:{on.id}"))
    runtime = await _prepare(db, agent, authz)
    assert _ids(runtime.plan.tool_links) == {on.id}
    assert {q.user for q in client.queries if q.object == f"tool:{off.id}"} == {f"user:{OWNER}"}


async def test_unavailable_openfga_fails_closed(db: AsyncSession) -> None:
    agent = await shared_agent(db, mode="fixed")
    await _link_tool(db, agent, await add_tool(db, owner=THIRD_USER_ID))
    authz, _ = _service(down=True)
    with pytest.raises(AppError) as caught:
        await _prepare(db, agent, authz)
    assert (caught.value.status, caught.value.code) == (503, "authz_unavailable")


async def test_a_required_broken_link_blocks_the_run(db: AsyncSession) -> None:
    agent = await shared_agent(db, mode="fixed")
    tool = await add_tool(db, owner=OWNER)
    await _link_tool(db, agent, tool, link_status="broken", required=True)
    authz, _ = _service(*_base(agent))
    with pytest.raises(AppError) as caught:
        await _prepare(db, agent, authz)
    blocked = _item(agent, "tool", result="delegation_broken", reason="broken")
    assert caught.value.code == "agent_not_ready"
    assert caught.value.details == {"overall": "blocked", "items": [blocked]}


async def test_a_type_not_yet_enforced_is_not_ready_instead_of_hidden(db: AsyncSession) -> None:
    agent = await shared_agent(db, mode="fixed")
    await _link_tool(db, agent, await add_tool(db, owner=THIRD_USER_ID))
    client = RecordingClient(allowed=frozenset(_base(agent)))
    authz = AuthzService(client, mode="enforce", enforce_types=frozenset({"agent"}))  # type: ignore[arg-type]
    with pytest.raises(AppError) as caught:
        await _prepare(db, agent, authz)
    assert (caught.value.status, caught.value.code) == (409, "agent_not_ready")
