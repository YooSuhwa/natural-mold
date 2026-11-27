from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_scope import can_read_conversation_run_id
from app.authz.org_context import OrgContext
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.independent_chat_job import IndependentChatJob
from app.models.message_event import MessageEvent
from app.routers.conversation_agent_protocol_state_snapshot import load_thread_state_snapshot
from app.schemas.conversation import MessageResponse
from app.services.chat.interrupts import _hydrate_pending_interrupt_tool_calls
from app.services.conversation_protocol_replay import (
    LiveProtocolEventsRequest,
    load_live_protocol_events,
)
from tests.authz.test_independent_job_authority import delegation as delegation
from tests.test_independent_child_read_scope import _child_run

pytestmark = pytest.mark.asyncio


async def test_only_managed_child_turns_contribute_trace_metadata_and_events(
    db: AsyncSession,
    client: AsyncClient,
    delegation: tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob],
) -> None:
    context, _, initial, job = delegation
    initial.status, initial.is_active = "interrupted", False
    approved_resume = _child_run(db, job, source="resume", parent_id=initial.id)
    unrelated = _child_run(db, job, source="start", parent_id=None)
    await db.flush()
    shared_message, unrelated_message = str(uuid4()), str(uuid4())
    for key, linked, content in (
        (str(initial.id), [shared_message], "owned first turn"),
        (str(approved_resume.id), [], "owned resumed turn"),
        (str(unrelated.id), [shared_message, unrelated_message], "unrelated payload"),
        ("legacy-without-a-run", [], "unattributed payload"),
    ):
        db.add(
            MessageEvent(
                conversation_id=job.child_conversation_id,
                assistant_msg_id=key,
                external_trace_id=f"external-{key}",
                linked_message_ids=linked,
                events=[
                    {"id": f"{key}-1", "event": "message_start", "data": {"id": key}},
                    {"id": f"{key}-2", "event": "message_end", "data": {"content": content}},
                ],
            )
        )
    await db.commit()
    allowed = {str(initial.id), str(approved_resume.id)}
    base = f"/api/conversations/{job.child_conversation_id}"
    traces = await client.get(f"{base}/traces")
    assert traces.status_code == 200
    assert {item["assistant_msg_id"] for item in traces.json()} == allowed
    debug = await client.get(f"{base}/debug/traces")
    assert debug.status_code == 200
    assert {item["moldy_run_id"] for item in debug.json()["traces"]} == allowed
    for key in (str(unrelated.id), f"external-{unrelated.id}", "legacy-without-a-run"):
        assert (await client.get(f"{base}/debug/traces/{key}")).status_code == 404
    links = await client.get(
        f"{base}/run-message-links",
        params=[("message_id", shared_message), ("message_id", unrelated_message)],
    )
    assert links.status_code == 200
    assert links.json() == [{"run_id": str(initial.id), "message_id": shared_message}]

    conversation = await db.get(Conversation, job.child_conversation_id)
    assert conversation is not None
    parent = await db.get(Conversation, job.parent_conversation_id)
    assert parent is not None
    assert await can_read_conversation_run_id(db, parent, None, context)
    assert not await can_read_conversation_run_id(db, conversation, None, context)
    protocol_events = await load_live_protocol_events(
        LiveProtocolEventsRequest(db, conversation, context, ())
    )
    assert {event["run_id"] for event in protocol_events} == allowed
    snapshot = await load_thread_state_snapshot(conversation, context, db)
    messages = snapshot.values["messages"]
    assert {msg["content"] for msg in messages} == {"owned first turn", "owned resumed turn"}


async def test_pending_cards_restore_only_from_attributed_child_traces(
    db: AsyncSession,
    delegation: tuple[OrgContext, ConversationRun, ConversationRun, IndependentChatJob],
) -> None:
    context, _, initial, job = delegation
    unrelated = _child_run(db, job, source="start", parent_id=None)
    await db.flush()
    messages = []
    for run in (initial, unrelated):
        msg_id = uuid4()
        call = {"name": "write_file", "args": {"file_path": "draft.md"}}
        messages.append(
            MessageResponse(
                id=msg_id,
                conversation_id=job.child_conversation_id,
                role="assistant",
                content="",
                tool_calls=[{"id": f"call-{msg_id}", **call}],
                created_at=initial.created_at,
            )
        )
        db.add(
            MessageEvent(
                conversation_id=job.child_conversation_id,
                assistant_msg_id=str(run.id),
                linked_message_ids=[str(msg_id)],
                events=[
                    {
                        "event": "interrupt",
                        "data": {
                            "interrupt_id": f"pause-{run.id}",
                            "action_requests": [call],
                            "review_configs": [{"allowed_decisions": ["approve", "reject"]}],
                        },
                    }
                ],
            )
        )
    await db.commit()
    await _hydrate_pending_interrupt_tool_calls(
        db,
        conversation_id=job.child_conversation_id,
        responses=messages,
        independent_context=context,
    )
    restored, untouched = messages
    assert restored.tool_calls and restored.tool_calls[0]["name"] == "request_approval"
    assert untouched.tool_calls and untouched.tool_calls[0]["name"] == "write_file"
