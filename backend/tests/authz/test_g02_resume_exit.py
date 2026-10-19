"""G-02: a resume that only ends the run passes the blocked gate (plan 12.3; G-02).

``auth_required`` ``cancel`` and HITL reject-all answers skip ``require_agent_ready`` so
the user can always leave a pending interrupt of a ``blocked`` agent; ``retry``, approvals
and mixed answers keep ``409 agent_not_ready``.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.runtime import get_authz
from app.exceptions import AppError
from app.models.conversation_run import ConversationRun
from app.routers import chat_resume_entries, conversation_protocol_fork
from app.services import readiness_cache
from app.services.readiness_gate import abandons_run
from tests.authz.d04_support import add_tool
from tests.authz.e01_support import THIRD_USER_ID, add_agent, add_member, seed
from tests.authz.e08_support import add_conversation
from tests.authz.f05_support import CALLER
from tests.authz.g01_support import enforce, link_tool, runnable
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID

CANCEL = {"type": "respond", "message": "cancel"}
REJECT = {"type": "reject", "message": "no"}


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> AsyncIterator[None]:
    await seed(db)
    await add_member(db, THIRD_USER_ID)
    readiness_cache.invalidate_all()
    yield
    readiness_cache.invalidate_all()


@pytest.mark.parametrize(
    ("answers", "exits"),
    [
        ([CANCEL], True),
        ([{"decisions": [CANCEL]}], True),
        ([{"decisions": [REJECT, REJECT]}], True),
        ([{"decisions": [CANCEL]}, {"decisions": [REJECT]}], True),
        ([{"type": "respond", "message": "retry"}], False),
        ([{"type": "respond", "message": "skip"}], False),
        ([{"decisions": [REJECT, {"type": "approve"}]}], False),
        ([{"decisions": []}], False),
        ([{"decisions": [CANCEL]}, {"decisions": [{"type": "approve"}]}], False),
        ([], False),
        ([None], False),
    ],
)
def test_abandons_run(answers: list[object], exits: bool) -> None:
    assert abandons_run(answers) is exits


async def _blocked_conversation(db: AsyncSession) -> tuple[Any, UUID]:
    """My agent with a disabled required tool (blocked only by readiness) + an interrupt."""
    agent = await add_agent(db, owner=CALLER)
    tool = await add_tool(db, owner=CALLER)
    tool.enabled = False
    await link_tool(db, agent, tool, required=True)
    conversation_id = await add_conversation(db, agent, user=CALLER, last_activity=_now())
    db.add(
        ConversationRun(
            conversation_id=conversation_id,
            agent_id=agent.id,
            user_id=CALLER,
            org_id=TEST_ORG_ID,
            tenant_id=TEST_TENANT_ID,
            source="chat",
            status="interrupted",
            interrupt_id="i-1",
            completed_at=_now().replace(tzinfo=None),
        )
    )
    await db.commit()
    return agent, conversation_id


@pytest.fixture
def passed_gate(test_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """After the gate the resume rebuilds the agent: stop there with a 418."""

    async def stop(*_: Any, **__: Any) -> Any:
        raise AppError(code="gate_passed", message="gate passed", status=418)

    monkeypatch.setattr(conversation_protocol_fork, "resolve_agent_context", stop)
    monkeypatch.setattr(chat_resume_entries.chat_service, "get_owned_conversation_with_agent", stop)

    def install(agent: Any) -> None:
        service = enforce(monkeypatch, *runnable(agent, account=CALLER))
        test_app.dependency_overrides[get_authz] = lambda: service

    yield install
    test_app.dependency_overrides.pop(get_authz, None)


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (CANCEL, 418),
        ({"decisions": [REJECT]}, 418),
        ({"type": "respond", "message": "retry"}, 409),
        ({"decisions": [{"type": "approve"}]}, 409),
    ],
)
async def test_protocol_input_respond(
    client: AsyncClient, db: AsyncSession, passed_gate: Any, response: object, status: int
) -> None:
    agent, conversation_id = await _blocked_conversation(db)
    passed_gate(agent)
    command = {
        "id": 1,
        "method": "input.respond",
        "params": {"interrupt_id": "i-1", "response": response},
    }
    url = f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands"
    result = await client.post(url, json=command)
    assert result.status_code == status, result.text
    if status == 409:
        assert result.json()["error"]["code"] == "agent_not_ready"


@pytest.mark.parametrize(("decision", "status"), [(REJECT, 418), ({"type": "approve"}, 409)])
async def test_rest_resume(
    client: AsyncClient, db: AsyncSession, passed_gate: Any, decision: object, status: int
) -> None:
    agent, conversation_id = await _blocked_conversation(db)
    passed_gate(agent)
    url = f"/api/conversations/{conversation_id}/messages/resume"
    result = await client.post(url, json={"decisions": [decision]})
    assert result.status_code == status, result.text


def _now() -> datetime:
    return datetime.now(UTC)
