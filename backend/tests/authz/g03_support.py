"""Seeds for the G-03/G-06 resume tests: an interrupted run with its pending interrupt."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_events import stored_protocol_event
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from tests.conftest import TEST_USER_ID
from tests.test_conversation_agent_protocol_commands import _seed_protocol_conversation

__all__ = ["seed_conversation"]
seed_conversation = _seed_protocol_conversation


def auth_payload(attempt: int = 1) -> dict[str, Any]:
    item = {
        "resource_type": "mcp_server",
        "resource_name": "Slack",
        "tool_names": ["post_message"],
        "credential_id": "cred-1",
        "fixable_by": "me",
        "connect": {"kind": "oauth"},
    }
    return {
        "type": "auth_required",
        "reason": "expired",
        "items": [item],
        "allowed_decisions": ["respond"],
        "attempt": attempt,
    }


def approval_payload() -> dict[str, Any]:
    return {
        "action_requests": [
            {"name": "execute_in_skill", "args": {"command": "python a.py"}},
            {"name": "send_email", "args": {"to": "a@example.com"}},
        ],
        "review_configs": [
            {"action_name": "execute_in_skill", "allowed_decisions": ["approve", "reject"]},
            {"action_name": "send_email", "allowed_decisions": ["approve", "edit", "reject"]},
        ],
    }


async def seed_interrupted(
    db: AsyncSession, conversation: Conversation, *payloads: dict[str, Any], iid: str = "intr-1"
) -> uuid.UUID:
    """An interrupted run whose events asked ``payloads`` in order under ``iid``."""

    run_id = uuid.uuid4()
    db.add(
        ConversationRun(
            id=run_id,
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=TEST_USER_ID,
            source="chat",
            status="interrupted",
            is_active=False,
            interrupt_id=iid,
        )
    )
    events = [
        stored_protocol_event(
            run_id=str(run_id),
            thread_id=str(conversation.id),
            seq=index + 1,
            method="input.requested",
            namespace=[],
            data={"interrupt_id": iid, "payload": payload},
            event_id=f"input-{index}",
        )
        for index, payload in enumerate(payloads)
    ]
    db.add(
        MessageEvent(
            conversation_id=conversation.id,
            assistant_msg_id=str(run_id),
            events=events,
            status="completed",
        )
    )
    await db.commit()
    return run_id


def commands_url(conversation: Conversation) -> str:
    return f"/api/conversations/{conversation.id}/langgraph/threads/{conversation.id}/commands"


def respond_command(response: Any, iid: str = "intr-1") -> dict[str, Any]:
    return {
        "id": "resume-cmd",
        "method": "input.respond",
        "params": {"namespace": [], "interrupt_id": iid, "response": response},
    }


async def resume_runs(db: AsyncSession, conversation: Conversation) -> list[ConversationRun]:
    rows = await db.scalars(
        select(ConversationRun).where(
            ConversationRun.conversation_id == conversation.id,
            ConversationRun.source == "resume",
        )
    )
    return list(rows)
