"""G-02: chat start/send/resume answer ``409 agent_not_ready`` before any row (plan 12.2, 12.3).

A ``blocked`` agent (12.2: a required ``no_permission``/``owner_must_connect``/
``unavailable`` item) is refused with the 12.3 body (``readiness_service.present``: a runner
sees counts, not the rows it cannot view) before a conversation or run row is written.
``needs_connection`` and ``limited`` start (the connection guard asks in-run, F-07). Legacy
(nothing enforced) never assesses.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import runtime
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.exceptions import AppError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import readiness_cache, readiness_gate, readiness_service
from tests.authz.authz_service_testkit import RecordingClient
from tests.authz.d04_support import add_tool
from tests.authz.e01_support import THIRD_USER_ID, add_agent, add_member, seed
from tests.authz.e08_support import add_conversation
from tests.authz.f05_support import CALLER, per_user_tool, shared_agent
from tests.authz.g01_support import AS_CALLER, enforce, link_tool, runnable
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TestSession


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> AsyncIterator[None]:
    await seed(db)
    await add_member(db, THIRD_USER_ID)
    readiness_cache.invalidate_all()
    yield
    readiness_cache.invalidate_all()


@pytest.fixture
def install(test_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Any:
    def _install(*allowed: tuple[str, str, str]) -> AuthzService:
        service = enforce(monkeypatch, *allowed)
        test_app.dependency_overrides[get_authz] = lambda: service
        return service

    yield _install
    test_app.dependency_overrides.pop(get_authz, None)


async def _count(model: Any, agent_id: UUID) -> int:
    async with TestSession() as db:
        return int(await db.scalar(select(func.count()).where(model.agent_id == agent_id)) or 0)


async def _run_count(conversation_id: UUID) -> int:
    async with TestSession() as db:
        query = select(func.count()).where(ConversationRun.conversation_id == conversation_id)
        return int(await db.scalar(query) or 0)


async def _blocked_shared(db: AsyncSession) -> tuple[Any, Any]:
    """A runnable shared agent whose required tool the caller may not use."""
    agent = await shared_agent(db, mode="per_user")
    denied = await add_tool(db, owner=THIRD_USER_ID, name="secret")
    await link_tool(db, agent, denied, required=True)
    return agent, denied


def _assert_runner_body(response: Any, agent: Any, denied: Any) -> None:
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "agent_not_ready"
    assert str(denied.id) not in response.text and "secret" not in response.text
    details = error["details"]
    assert details["overall"] == "blocked" and details["identity_mode"] == "per_user"
    assert {
        "kind": "tool",
        "count": 1,
        "required": True,
        "result": "no_permission",
        "action": {"type": "request_access", "via": "agent_manager"},
    } in details["items"]


async def test_start_refuses_a_blocked_agent_before_any_conversation(
    client: AsyncClient, db: AsyncSession, install: Any
) -> None:
    agent, denied = await _blocked_shared(db)
    install(*runnable(agent))
    response = await client.post(
        f"/api/agents/{agent.id}/conversations/start", json={"content": "hi"}
    )
    _assert_runner_body(response, agent, denied)
    assert await _count(Conversation, agent.id) == 0


async def test_send_refuses_with_the_same_body_and_writes_no_run(
    client: AsyncClient, db: AsyncSession, install: Any
) -> None:
    agent, denied = await _blocked_shared(db)
    conversation_id = await add_conversation(
        db, agent, user=CALLER, last_activity=datetime.now(UTC)
    )
    install(*runnable(agent))
    response = await client.post(
        f"/api/conversations/{conversation_id}/messages", json={"content": "hi"}
    )
    _assert_runner_body(response, agent, denied)
    assert await _run_count(conversation_id) == 0


async def test_protocol_run_start_refuses_before_any_run(
    client: AsyncClient, db: AsyncSession, install: Any
) -> None:
    agent, denied = await _blocked_shared(db)
    conversation_id = await add_conversation(
        db, agent, user=CALLER, last_activity=datetime.now(UTC)
    )
    install(*runnable(agent))
    command = {
        "id": 1,
        "method": "run.start",
        "params": {"assistant_id": str(agent.id), "input": {"messages": []}},
    }
    url = f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands"
    response = await client.post(url, json=command)
    assert response.status_code == 409, response.text
    assert "agent_not_ready" in response.text and str(denied.id) not in response.text
    assert await _run_count(conversation_id) == 0


async def test_my_agent_with_a_disabled_required_tool_is_blocked_with_ids(
    client: AsyncClient, db: AsyncSession, install: Any
) -> None:
    agent = await add_agent(db, owner=CALLER)
    tool = await add_tool(db, owner=CALLER, name="web")
    tool.enabled = False
    await link_tool(db, agent, tool, required=True)
    install(*runnable(agent, account=CALLER))  # the model is the only other item: ready
    response = await client.post(
        f"/api/agents/{agent.id}/conversations/start", json={"content": "hi"}
    )
    assert response.status_code == 409, response.text
    item = {"kind": "tool", "id": str(tool.id), "agent_id": str(agent.id), "name": "web",
            "required": True, "result": "unavailable", "action": None}  # fmt: skip
    items = response.json()["error"]["details"]["items"]
    assert item in items and [i["result"] for i in items].count("ready") == 1
    assert await _count(Conversation, agent.id) == 0


@pytest.mark.parametrize("required", [True, False])
async def test_needs_connection_and_limited_are_not_refused(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, required: bool
) -> None:
    agent = await shared_agent(db, mode="per_user")
    tool = await per_user_tool(db, agent)  # the caller has no binding: connect_by_me
    use = (AS_CALLER, "can_use", f"tool:{tool.id}")
    if not required:
        await link_tool(db, agent, await add_tool(db, owner=THIRD_USER_ID))  # optional denied
    service = enforce(monkeypatch, *runnable(agent), use)
    context = unit_context(CALLER)
    readiness = await readiness_service.assess_agent(db, service, context, agent.id)
    assert readiness.overall in ("needs_connection", "limited")
    await readiness_gate.require_agent_ready(db, context, agent.id, authz=service)


async def test_legacy_never_assesses(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = await add_agent(db, owner=CALLER)
    tool = await add_tool(db, owner=CALLER)
    tool.enabled = False
    await link_tool(db, agent, tool, required=True)

    async def boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("legacy must not assess readiness")

    monkeypatch.setattr(readiness_service, "assess_agent", boom)
    service = AuthzService(RecordingClient(allowed=frozenset()), mode="legacy")  # type: ignore[arg-type]
    monkeypatch.setattr(runtime, "_current", service)
    await readiness_gate.require_agent_ready(db, unit_context(CALLER), agent.id)


async def test_the_gate_raises_the_12_3_body(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent, _ = await _blocked_shared(db)
    service = enforce(monkeypatch, *runnable(agent))
    context = unit_context(CALLER)
    with pytest.raises(AppError) as caught:
        await readiness_gate.require_agent_ready(db, context, agent.id, authz=service)
    assert caught.value.status == 409 and caught.value.code == "agent_not_ready"
    readiness = await readiness_service.assess_agent(db, service, context, agent.id)
    assert caught.value.details == readiness_service.present(readiness)
