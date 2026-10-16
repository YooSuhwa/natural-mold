"""D-08: Builder Assistant routes and tools decide through ``AuthzService`` (plan 11.7)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.agent_runtime.assistant.tools.read_tools import build_read_tools
from app.agent_runtime.assistant.tools.write_tools import build_write_tools
from app.authz.service import AuthzService, ShadowSample, Verdict
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.tool import Tool
from tests.authz.agent_route_fixture import (
    ME,
    OTHER_USER_ID,
    DownAuthz,
    authz_override,
    enforce,
    seed_agent,
    shadow,
)
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession

NOT_FOUND = "에이전트를 찾을 수 없습니다."


@pytest.fixture
def tool_sessions() -> Iterator[None]:
    with (
        patch("app.agent_runtime.assistant.tools.read_tools.async_session_factory", TestSession),
        patch("app.agent_runtime.assistant.tools.write_tools.async_session_factory", TestSession),
    ):
        yield


def _runtime(monkeypatch: pytest.MonkeyPatch, service: AuthzService) -> None:
    monkeypatch.setattr("app.authz.runtime._current", service)


def _tool(agent_id: str, name: str):
    ctx = unit_context(TEST_USER_ID)
    tools = [
        *build_read_tools(None, uuid.UUID(agent_id), ctx),  # type: ignore[arg-type]
        *build_write_tools(None, uuid.UUID(agent_id), ctx),  # type: ignore[arg-type]
    ]
    return next(tool for tool in tools if tool.name == name)


async def _stream(*args, **kwargs):
    yield "event: message_end\ndata: {}\n\n"


# --- routes: RES(agent, can_edit) -------------------------------------------------


async def _post(client: AsyncClient, agent_id: str):
    with patch("app.services.assistant_service.stream_assistant_message", side_effect=_stream):
        return await client.post(f"/api/agents/{agent_id}/assistant/message", json={"content": "x"})


async def test_legacy_route_keeps_owner_only_answer(client: AsyncClient):
    mine = await seed_agent(owner=TEST_USER_ID)
    shared = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    assert (await _post(client, mine)).status_code == 200
    denied = await _post(client, shared)
    assert denied.status_code == 404 and denied.json()["error"]["code"] == "AGENT_NOT_FOUND"


async def test_enforce_route_allows_shared_editor_and_maps_8_4(
    client: AsyncClient, test_app: FastAPI
):
    shared = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    agent = uuid.UUID(shared)
    with authz_override(test_app, enforce((ME, "can_edit", agent), (ME, "can_run", agent))):
        assert (await _post(client, shared)).status_code == 200
    with authz_override(test_app, enforce((ME, "can_run", agent))):
        forbidden = await _post(client, shared)
    with authz_override(test_app, enforce()):
        hidden = await _post(client, shared)
    with authz_override(test_app, AuthzService(DownAuthz(), mode="enforce")):
        down = await _post(client, shared)
    assert forbidden.status_code == 403
    assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "AGENT_NOT_FOUND"
    assert down.status_code == 503


async def test_shadow_route_answers_legacy_and_samples(client: AsyncClient, test_app: FastAPI):
    mine = await seed_agent(owner=TEST_USER_ID)
    samples: list[ShadowSample] = []
    with authz_override(test_app, shadow(samples)):
        assert (await _post(client, mine)).status_code == 200
    assert any(
        s.permission == "can_edit" and s.obj == f"agent:{mine}" and s.legacy is Verdict.ALLOW
        for s in samples
    )


# --- tools: per-call re-check via runtime_authz() ----------------------------------


async def test_legacy_tool_keeps_owner_scope(tool_sessions, monkeypatch):
    shared = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    mine = await seed_agent(owner=TEST_USER_ID, name="mine")
    assert await _tool(shared, "get_agent_config").ainvoke({}) == NOT_FOUND
    assert json.loads(await _tool(mine, "get_agent_config").ainvoke({}))["name"] == "mine"


async def test_enforce_read_tool_reads_shared_agent_in_owner_scope(tool_sessions, monkeypatch):
    shared = await seed_agent(owner=OTHER_USER_ID, name="shared", grant_to_me=True)
    agent = uuid.UUID(shared)
    _runtime(monkeypatch, enforce((ME, "can_view_config", agent), (ME, "can_run", agent)))
    assert json.loads(await _tool(shared, "get_agent_config").ainvoke({}))["name"] == "shared"
    _runtime(monkeypatch, enforce((ME, "can_run", agent)))
    assert await _tool(shared, "get_agent_config").ainvoke({}) == NOT_FOUND


async def test_enforce_write_tool_needs_can_edit(tool_sessions, monkeypatch):
    shared = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    agent = uuid.UUID(shared)
    write = _tool(shared, "update_chat_openers")
    _runtime(monkeypatch, enforce((ME, "can_view_config", agent), (ME, "can_run", agent)))
    assert await write.ainvoke({"openers": ["new?"]}) == NOT_FOUND
    _runtime(monkeypatch, enforce((ME, "can_edit", agent), (ME, "can_run", agent)))
    assert await write.ainvoke({"openers": ["new?"]}) != NOT_FOUND
    async with TestSession() as db:
        row = await db.get(Agent, agent)
    assert row is not None and row.opener_questions == ["new?"]


async def test_enforce_tool_outage_fails_closed(tool_sessions, monkeypatch):
    mine = await seed_agent(owner=TEST_USER_ID)
    _runtime(monkeypatch, AuthzService(DownAuthz(), mode="enforce"))
    with pytest.raises(AppError) as raised:
        await _tool(mine, "get_agent_config").ainvoke({})
    assert raised.value.status == 503


async def test_shadow_tool_samples_actor_permission(tool_sessions, monkeypatch):
    mine = await seed_agent(owner=TEST_USER_ID)
    samples: list[ShadowSample] = []
    _runtime(monkeypatch, shadow(samples))
    assert await _tool(mine, "get_recursion_limit").ainvoke({}) != NOT_FOUND
    assert await _tool(mine, "update_recursion_limit").ainvoke({"limit": 40}) != NOT_FOUND
    seen = {(s.permission, s.legacy) for s in samples if s.obj == f"agent:{mine}"}
    assert seen == {("can_view_config", Verdict.ALLOW), ("can_edit", Verdict.ALLOW)}


async def test_enforce_link_needs_resource_permission(tool_sessions, monkeypatch):
    mine = await seed_agent(owner=TEST_USER_ID)
    async with TestSession() as db:
        tool = Tool(name="Calc", definition_key="builtin:calc", is_system=True)
        db.add(tool)
        await db.commit()
        tool_obj = f"tool:{tool.id}"
    agent = {(ME, "can_edit", f"agent:{mine}"), (ME, "can_run", f"agent:{mine}")}
    types = frozenset({"agent", "tool"})
    add = _tool(mine, "add_tool_to_agent")
    _runtime(monkeypatch, _service(agent, types))
    assert await add.ainvoke({"tool_names": ["Calc"]}) == "도구를 찾을 수 없습니다: Calc"
    _runtime(monkeypatch, _service(agent | {(ME, "can_use", tool_obj)}, types))
    assert await add.ainvoke({"tool_names": ["Calc"]}) == "도구 추가 완료: Calc"


def _service(allowed: set[tuple[str, str, str]], types: frozenset[str]) -> AuthzService:
    from app.authz.fake import FakeAuthz

    return AuthzService(FakeAuthz(allowed=frozenset(allowed)), mode="enforce", enforce_types=types)


async def test_enforce_grantee_identity_switch_sees_owner_triggers(tool_sessions, monkeypatch):
    from app.models.agent_trigger import AgentTrigger
    from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID

    shared = await seed_agent(owner=OTHER_USER_ID, grant_to_me=True)
    agent = uuid.UUID(shared)
    async with TestSession() as db:
        db.add(
            AgentTrigger(
                agent_id=agent,
                user_id=OTHER_USER_ID,
                org_id=TEST_ORG_ID,
                tenant_id=TEST_TENANT_ID,
                trigger_type="interval",
                schedule_config={"interval_minutes": 60},
                input_message="run",
                status="active",
            )
        )
        await db.commit()
    _runtime(monkeypatch, enforce((ME, "can_edit", agent), (ME, "can_run", agent)))
    refused = await _tool(shared, "update_agent_identity_mode").ainvoke(
        {"identity_mode": "per_user"}
    )
    assert "활성 스케줄" in refused
    async with TestSession() as db:
        row = await db.get(Agent, agent)
    assert row is not None and row.identity_mode == "fixed"
