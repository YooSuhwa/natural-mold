"""G-05: a legacy messages reload rebuilds the pending ``auth_required`` card.

Mirrors ``tests/test_chat_service.py`` approval hydration: the trace keeps the legacy
SSE ``interrupt`` event (now discriminated, ``type=auth_required``); the persisted
assistant message gets the ``moldy_auth_required`` card the frontend builds live, with
the latest ``attempt``, until the run moved on or the user answered.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.conversation import MessageResponse
from app.services import chat_service, trace_storage
from app.services.chat_service import create_conversation
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.test_chat_service import _seed

_ITEMS = [
    {
        "resource_type": "mcp_server",
        "resource_name": "Slack",
        "tool_names": ["post_message"],
        "credential_id": "c1",
        "fixable_by": "me",
        "connect": {"kind": "oauth"},
    }
]


def _auth_event(run_id: str, seq: int, attempt: int) -> dict[str, Any]:
    return {
        "id": f"{run_id}-{seq}",
        "event": "interrupt",
        "data": {
            "interrupt_id": "intr-auth",
            "type": "auth_required",
            "reason": "expired",
            "items": _ITEMS,
            "attempt": attempt,
            "allowed_decisions": ["respond"],
        },
    }


async def _turn(db: AsyncSession, *, attempts: list[int]) -> tuple[uuid.UUID, MessageResponse]:
    agent_id = await _seed(db)
    conv = await create_conversation(db, agent_id, title="auth", context=unit_context(TEST_USER_ID))
    msg_id = uuid.uuid4()
    response = MessageResponse(
        id=msg_id,
        conversation_id=conv.id,
        role="assistant",
        content="",
        tool_calls=[{"id": "call-1", "name": "post_message", "args": {"text": "hi"}}],
        tool_call_id=None,
        created_at=conv.created_at,
    )
    for attempt in attempts:  # each re-ask is its own (resume) run record
        run_id = str(uuid.uuid4())
        await trace_storage.append_events(
            db,
            conversation_id=conv.id,
            assistant_msg_id=run_id,
            events_chunk=[
                {"id": f"{run_id}-1", "event": "message_start", "data": {"id": run_id}},
                _auth_event(run_id, 2, attempt),
            ],
        )
        await trace_storage.finalize_turn(
            db, assistant_msg_id=run_id, raw_msg_ids=[str(msg_id)], conversation_id=conv.id
        )
    await db.commit()
    return conv.id, response


async def _hydrate(db: AsyncSession, conversation_id: uuid.UUID, *responses: MessageResponse):
    await chat_service._hydrate_pending_interrupt_tool_calls(  # noqa: SLF001
        db, conversation_id=conversation_id, responses=list(responses)
    )


@pytest.mark.asyncio
async def test_reload_rebuilds_the_card_with_the_latest_attempt(db: AsyncSession) -> None:
    conversation_id, response = await _turn(db, attempts=[1, 2])

    await _hydrate(db, conversation_id, response)

    calls = response.tool_calls or []
    assert [call["name"] for call in calls] == ["post_message", "moldy_auth_required"]
    card = calls[1]
    assert card["id"] == "intr-auth:0#2"
    assert card["args"] == {
        "reason": "expired",
        "items": _ITEMS,
        "attempt": 2,
        "approval_id": "intr-auth:0",
        "allowed_decisions": ["respond"],
        "hitl_interrupt_id": "intr-auth",
        "hitl_action_index": 0,
        "hitl_total_actions": 1,
    }


@pytest.mark.asyncio
async def test_no_card_once_the_user_answered(db: AsyncSession) -> None:
    conversation_id, response = await _turn(db, attempts=[1])
    skipped = MessageResponse(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        role="tool",
        content="[Connection Guard] post_message: skipped: skipped by the user",
        tool_call_id="call-1",
        created_at=response.created_at,
    )

    await _hydrate(db, conversation_id, response, skipped)

    assert [call["name"] for call in response.tool_calls or []] == ["post_message"]


@pytest.mark.asyncio
async def test_no_card_once_the_run_moved_on(db: AsyncSession) -> None:
    conversation_id, response = await _turn(db, attempts=[1])
    later = MessageResponse(
        id=uuid.uuid4(),
        conversation_id=conversation_id,
        role="assistant",
        content="posted",
        created_at=response.created_at,
    )

    await _hydrate(db, conversation_id, response, later)

    assert [call["name"] for call in response.tool_calls or []] == ["post_message"]
