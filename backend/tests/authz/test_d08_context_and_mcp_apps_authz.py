"""D-08: resource-context skills need ``can_view``; MCP App commands need ``can_invoke``."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.fake import FakeAuthz
from app.authz.resource_context_scope import authorize_context_skill
from app.authz.service import AuthzService, ShadowSample, Verdict
from app.exceptions import AppError
from app.mcp.apps_binding_store import McpAppsError, authorize_binding, record_runtime_binding
from app.mcp.domain import McpAppRuntimeContext
from app.models.authz import ResourceGrant
from app.models.mcp_app_invocation import McpAppInvocationBinding
from app.models.mcp_tool import McpTool
from app.models.skill import AgentSkillLink, Skill
from app.services import mcp_apps_service
from tests.authz.agent_route_fixture import ME, OTHER_USER_ID, DownAuthz, seed_agent
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession

if TYPE_CHECKING:
    from tests.authz.private_mcp_apps_fixture import McpAppCase

pytest_plugins = [
    "tests.authz.private_scope_fixture",
    "tests.authz.private_mcp_apps_fixture",
]


def _runtime(monkeypatch: pytest.MonkeyPatch, service: AuthzService) -> None:
    monkeypatch.setattr("app.authz.runtime._current", service)


def _enforce(*allowed: tuple[str, str, str], types: frozenset[str]) -> AuthzService:
    fake = FakeAuthz(allowed=frozenset(allowed))
    return AuthzService(fake, mode="enforce", enforce_types=types)


# --- chat resource context: skill#can_view ------------------------------------------


async def _linked_skill(*, owner: uuid.UUID, grant_to_me: bool = False) -> tuple[str, Skill]:
    agent_id = await seed_agent(owner=TEST_USER_ID)
    async with TestSession() as db:
        skill = Skill(
            user_id=owner, org_id=TEST_ORG_ID, tenant_id=TEST_TENANT_ID, name="S", slug="s"
        )
        db.add(skill)
        await db.flush()
        db.add(AgentSkillLink(agent_id=uuid.UUID(agent_id), skill_id=skill.id))
        if grant_to_me:
            db.add(
                ResourceGrant(
                    org_id=TEST_ORG_ID,
                    tenant_id=TEST_TENANT_ID,
                    resource_type="skill",
                    resource_id=skill.id,
                    relation="viewer",
                    principal_type="user",
                    principal_id=TEST_USER_ID,
                )
            )
        await db.commit()
        return agent_id, skill


async def _decide(agent_id: str, skill: Skill, *, owned: bool) -> Skill | None:
    async with TestSession() as db:
        legacy = await db.get(Skill, skill.id) if owned else None
        return await authorize_context_skill(
            db, unit_context(TEST_USER_ID), uuid.UUID(agent_id), skill.id, legacy=legacy
        )


async def test_context_skill_legacy_and_shadow_keep_owner_answer(monkeypatch):
    agent_id, skill = await _linked_skill(owner=TEST_USER_ID)
    assert (await _decide(agent_id, skill, owned=True)) is not None
    assert (await _decide(agent_id, skill, owned=False)) is None
    samples: list[ShadowSample] = []
    _runtime(monkeypatch, AuthzService(FakeAuthz(), mode="shadow", shadow_hook=samples.append))
    assert (await _decide(agent_id, skill, owned=True)) is not None
    assert [(s.permission, s.obj, s.legacy) for s in samples] == [
        ("can_view", f"skill:{skill.id}", Verdict.ALLOW)
    ]


async def test_context_skill_enforce_decides_shared_and_denied(monkeypatch):
    agent_id, skill = await _linked_skill(owner=OTHER_USER_ID, grant_to_me=True)
    obj = f"skill:{skill.id}"
    types = frozenset({"skill"})
    _runtime(monkeypatch, _enforce((ME, "can_view", obj), (ME, "can_use", obj), types=types))
    allowed = await _decide(agent_id, skill, owned=False)
    assert allowed is not None and allowed.id == skill.id
    _runtime(monkeypatch, _enforce((ME, "can_use", obj), types=types))
    assert (await _decide(agent_id, skill, owned=False)) is None  # 403 -> uniform not found
    _runtime(monkeypatch, AuthzService(DownAuthz(), mode="enforce", enforce_types=types))
    with pytest.raises(AppError) as raised:
        await _decide(agent_id, skill, owned=False)
    assert raised.value.status == 503


# --- MCP App commands: mcp_tool#can_invoke ------------------------------------------


async def _list(case: McpAppCase):
    token = case.client.cookies.get("moldy_csrf")
    url = f"/api/conversations/{case.conversation_id}/runs/{case.run_id}/mcp-apps/fixture-call"
    return await case.client.post(
        url,
        json={"method": "resources/list", "params": {}},
        headers={"X-CSRF-Token": token or ""},
    )


async def test_mcp_app_command_needs_can_invoke(
    db: AsyncSession, mcp_app_case: McpAppCase, monkeypatch: pytest.MonkeyPatch
):
    case = mcp_app_case
    tool_id = await db.scalar(select(McpTool.id).where(McpTool.name == "fixture"))
    me = f"user:{case.context.user_id}"
    obj = f"mcp_tool:{tool_id}"
    types = frozenset({"mcp_tool"})
    assert (await _list(case)).status_code == 200  # legacy: the linked-tool lookup decides
    _runtime(monkeypatch, _enforce(types=types))
    denied = await _list(case)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "mcp_app_binding_revoked"
    _runtime(monkeypatch, _enforce((me, "can_invoke", obj), types=types))
    assert (await _list(case)).status_code == 200
    samples: list[ShadowSample] = []
    _runtime(monkeypatch, AuthzService(FakeAuthz(), mode="shadow", shadow_hook=samples.append))
    assert (await _list(case)).status_code == 200
    assert ("can_invoke", obj, Verdict.ALLOW) in {(s.permission, s.obj, s.legacy) for s in samples}


def _runtime_binding(case: McpAppCase, binding: McpAppInvocationBinding) -> McpAppRuntimeContext:
    assert binding.agent_id is not None
    return McpAppRuntimeContext(
        org_context=case.context,
        conversation_id=case.conversation_id,
        user_id=case.context.user_id,
        agent_id=binding.agent_id,
        credential_subject_user_id=case.context.user_id,
        mcp_server_id=binding.mcp_server_id,
        mcp_tool_id=binding.mcp_tool_id,
        remote_tool_name=binding.remote_tool_name,
        resource_uri=binding.resource_uri,
        tool_meta=binding.tool_meta,
    )


async def test_runtime_binding_mint_needs_can_invoke(
    db: AsyncSession, mcp_app_case: McpAppCase, monkeypatch: pytest.MonkeyPatch
):
    case = mcp_app_case
    binding = await db.scalar(select(McpAppInvocationBinding))
    assert binding is not None
    runtime = _runtime_binding(case, binding)
    me, obj = f"user:{case.context.user_id}", f"mcp_tool:{binding.mcp_tool_id}"
    types = frozenset({"mcp_tool"})
    _runtime(monkeypatch, _enforce(types=types))
    with pytest.raises(McpAppsError) as denied:
        await record_runtime_binding(
            runtime, str(case.run_id), "minted", None, None, session_factory=TestSession
        )
    assert (denied.value.code, denied.value.status_code) == ("mcp_app_tool_revoked", 403)
    _runtime(monkeypatch, _enforce((me, "can_invoke", obj), types=types))
    minted = await record_runtime_binding(
        runtime, str(case.run_id), "minted", None, None, session_factory=TestSession
    )
    assert minted is not None


async def test_bound_tool_call_needs_can_invoke(
    db: AsyncSession, mcp_app_case: McpAppCase, monkeypatch: pytest.MonkeyPatch
):
    case = mcp_app_case
    authorized = await authorize_binding(
        db,
        conversation_id=case.conversation_id,
        run_id=case.run_id,
        tool_call_id="fixture-call",
        context=case.context,
    )
    wire = AsyncMock(return_value={"success": True, "content": []})
    monkeypatch.setattr(mcp_apps_service, "_auth_for_context", AsyncMock(return_value=_NoAuth()))
    monkeypatch.setattr(mcp_apps_service, "call_mcp_tool_once", wire)
    me, obj = f"user:{case.context.user_id}", f"mcp_tool:{authorized.origin_tool.id}"
    types = frozenset({"mcp_tool"})
    _runtime(monkeypatch, _enforce(types=types))
    with pytest.raises(McpAppsError) as denied:
        await mcp_apps_service.call_bound_tool(db, authorized, tool_name="fixture", arguments={})
    assert (denied.value.code, denied.value.status_code) == ("mcp_app_tool_forbidden", 403)
    wire.assert_not_awaited()
    _runtime(monkeypatch, _enforce((me, "can_invoke", obj), types=types))
    await mcp_apps_service.call_bound_tool(db, authorized, tool_name="fixture", arguments={})
    wire.assert_awaited_once()


class _NoAuth:
    headers: dict[str, str] = {}
    credentials: dict[str, str] = {}
