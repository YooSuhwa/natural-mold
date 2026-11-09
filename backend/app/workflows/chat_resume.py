"""Resume a chat once the workflow its tool started has ended (workflow-tool-plan 3.2).

The API's leader scheduler sweeps runs that a chat started (``parent_conversation_id``) and
that have ended but whose chat was not resumed yet (``chat_resumed_at``). For each it finds
the chat's pending ``workflow_wait`` interrupt for that run and answers it through the same
``input.respond`` path a person's answer takes, as the approval expiry sweep does: in the API
process, so the resumed run streams to the person like any other. The answer carries only
the run's status; the tool reads the real result from the run itself.

A run whose chat is no longer waiting for it (the person moved on, the conversation is gone)
is marked resumed without doing anything. A failure to resume is logged and retried by the
next sweep.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select

from app.authz.org_context import OrgContext
from app.models.workflow import RUN_TERMINAL, WorkflowRun
from app.workflows.chat_tool import WAIT_TYPE

logger = logging.getLogger(__name__)

BATCH: Final = 20


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _due() -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]]:
    from app.database import async_session, platform_session

    async with platform_session("workflow.chat_resume", maker=async_session) as db:
        rows = await db.execute(
            select(WorkflowRun.id, WorkflowRun.org_id, WorkflowRun.tenant_id, WorkflowRun.user_id)
            .where(
                WorkflowRun.parent_conversation_id.is_not(None),
                WorkflowRun.chat_resumed_at.is_(None),
                WorkflowRun.status.in_(RUN_TERMINAL),
            )
            .order_by(WorkflowRun.finished_at, WorkflowRun.id)
            .limit(BATCH)
        )
        return [(r.id, r.org_id, r.tenant_id, r.user_id) for r in rows]


def _waiting_interrupt(tasks: list[Any], run_id: uuid.UUID) -> dict[str, Any] | None:
    for task in tasks:
        for pending in task["interrupts"]:
            value = pending.get("value")
            if (
                isinstance(value, dict)
                and value.get("type") == WAIT_TYPE
                and value.get("workflow_run_id") == str(run_id)
            ):
                return pending
    return None


async def resume_one(run_id: uuid.UUID, context: OrgContext) -> str:
    """Resume the chat waiting for this run; returns what happened (for logs and tests)."""
    from app.approvals.inbox import _protocol, _subject
    from app.authz.conversation_scope import load_private_conversation
    from app.database import async_session, tenant_session
    from app.exceptions import AppError
    from app.routers.conversation_agent_protocol_contracts import (
        AgentCommandParams,
        AgentCommandRequest,
    )
    from app.routers.conversation_agent_protocol_interrupts import load_pending_interrupt_tasks
    from app.routers.conversation_protocol_fork import _handle_input_respond_command

    async with tenant_session(context, maker=async_session) as db:
        run = await db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update())
        if run is None or run.chat_resumed_at is not None or run.parent_conversation_id is None:
            return "skipped"
        user = await _subject(db, run.user_id)
        conversation = None
        if user is not None:
            try:
                conversation = await load_private_conversation(
                    db, run.parent_conversation_id, context
                )
            except AppError:
                conversation = None
        pending = None
        if conversation is not None and user is not None:
            tasks = await load_pending_interrupt_tasks(
                db, conversation, user_id=user.id, context=context
            )
            pending = _waiting_interrupt(tasks, run.id)
        if pending is None or conversation is None or user is None:
            run.chat_resumed_at = _now()
            await db.commit()
            return "not_waiting"
        status = run.status
        command = AgentCommandRequest(
            id=f"workflow:{run.id}",
            method="input.respond",
            params=AgentCommandParams(
                interrupt_id=pending["id"],
                namespace=list(pending.get("ns") or []),
                response={"type": WAIT_TYPE, "status": status},
            ),
        )
        response = await _handle_input_respond_command(
            conversation=conversation,
            command=command,
            request=None,
            db=db,
            user=user,
            context=context,
            start_run=_protocol("start_conversation_run"),
            executor_fn=_protocol("resume_agent_stream_langgraph"),
            approval_channel="workflow",
        )
        body = json.loads(bytes(response.body))
        if body.get("type") == "error":
            logger.warning(
                "workflow %s finished but its chat was not resumed: %s", run_id, body.get("error")
            )
            await db.rollback()
            return "refused"
        fresh = await db.get(WorkflowRun, run_id)
        if fresh is not None:
            fresh.chat_resumed_at = _now()
        await db.commit()
        return "resumed"


async def resume_finished_chats() -> dict[str, int]:
    """The leader's sweep: one pass over ended runs a chat is waiting for."""
    counts: dict[str, int] = {}
    for run_id, org_id, tenant_id, user_id in await _due():
        context = OrgContext(org_id=org_id, tenant_id=tenant_id, user_id=user_id)
        try:
            outcome = await resume_one(run_id, context)
        except Exception:
            logger.exception("workflow chat resume failed run_id=%s", run_id)
            outcome = "error"
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


__all__ = ["resume_finished_chats", "resume_one"]
