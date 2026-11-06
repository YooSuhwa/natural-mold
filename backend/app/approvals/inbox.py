"""Deciding outside the chat: the approval inbox and the expiry sweep (plan 17.2).

An inbox decision goes through the very path the chat card's answer takes
(``_handle_input_respond_command``): the same allow-list check, the restoration of masked
arguments, the execution proof (company and organization membership, agent lifecycle),
the resumed run and the compare-and-set on the request, only with ``channel="inbox"``.
Before that, the request must still be pending and unexpired, and the run's checkpoint
must still hold the interrupt with the very arguments the decider was shown.

The sweep ends requests past ``expires_at`` as ``expired`` ("승인 만료"): the run resumes
with a rejection so the agent closes its turn, and the subject hears about it. Requests
an hour from expiry notify their decider once.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import service
from app.approvals.recording import INBOX_LINK
from app.approvals.waiters import abandon_best_effort
from app.authz.conversation_scope import load_private_conversation
from app.authz.notifications import Target, notify
from app.authz.org_context import OrgContext
from app.database import SessionTenant, async_session, platform_session, tenant_session
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation
from app.models.user import User

logger = logging.getLogger(__name__)

EXPIRED_MESSAGE: Final = "승인 요청이 만료되어 실행하지 않았습니다."


@dataclass(frozen=True, slots=True)
class InboxError(Exception):
    code: str
    message: str
    status: int = 409

    def as_app_error(self) -> AppError:
        return AppError(self.code, self.message, status=self.status)


def _protocol(name: str) -> Any:
    """Patchable collaborators of the chat protocol, read at call time (tests patch them)."""
    from app.routers import conversation_agent_protocol, conversation_agent_protocol_commands

    module = (
        conversation_agent_protocol
        if name == "start_conversation_run"
        else (conversation_agent_protocol_commands)
    )
    return getattr(module, name)


async def _pending_value(
    db: AsyncSession, conversation: Conversation, context: OrgContext, interrupt_id: str
) -> Any | None:
    from app.routers.conversation_agent_protocol_interrupts import (
        interrupts_from_tasks,
        load_pending_interrupt_tasks,
    )

    tasks = await load_pending_interrupt_tasks(
        db, conversation, user_id=context.user_id, context=context
    )
    found = next((i for i in interrupts_from_tasks(tasks) if i["id"] == interrupt_id), None)
    return None if found is None else found["value"]


async def close(db: AsyncSession, row: ApprovalRequest, reason: str) -> None:
    """Retire a pending request whose run no longer waits for it (no decision made)."""
    await db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.id == row.id, ApprovalRequest.status == "pending")
        .values(status="canceled", reason=reason, decided_at=service.now())
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    # Nothing resumes: the schedule or API run waiting on it ends instead of waiting forever.
    await abandon_best_effort(SessionTenant(row.tenant_id), [row.id])


def _check_decisions(row: ApprovalRequest, decisions: Sequence[Mapping[str, Any]]) -> None:
    if len(decisions) != len(row.actions):
        raise InboxError("invalid_decision", "one decision per action is required", 422)
    for decision, action in zip(decisions, row.actions, strict=True):
        if decision.get("type") not in action.get("allowed_decisions", []):
            raise InboxError("invalid_decision", f"{decision.get('type')} is not allowed here", 422)


async def _respond(
    db: AsyncSession,
    *,
    row: ApprovalRequest,
    conversation: Conversation,
    decisions: Sequence[Mapping[str, Any]],
    user: CurrentUser,
    context: OrgContext,
    request: Request | None,
    channel: str,
) -> None:
    from app.routers.conversation_agent_protocol_contracts import (
        AgentCommandParams,
        AgentCommandRequest,
    )
    from app.routers.conversation_protocol_fork import _handle_input_respond_command
    from app.services import chat_service

    await chat_service.require_executable_conversation(db, conversation.id, context)
    command = AgentCommandRequest(
        id=f"approval:{row.id}",
        method="input.respond",
        params=AgentCommandParams(
            interrupt_id=row.interrupt_id,
            namespace=list(row.namespace or []),
            response={"decisions": [dict(d) for d in decisions]},
        ),
    )
    response = await _handle_input_respond_command(
        conversation=conversation,
        command=command,
        request=request,
        db=db,
        user=user,
        context=context,
        start_run=_protocol("start_conversation_run"),
        executor_fn=_protocol("resume_agent_stream_langgraph"),
        approval_channel=channel,
    )
    body = json.loads(bytes(response.body))
    if body.get("type") == "error":
        error = body.get("error") or {}
        status = response.status_code if response.status_code >= 400 else 409
        raise InboxError(
            str(error.get("code") or "resume_refused"), str(error.get("message")), status
        )


async def decide(
    db: AsyncSession,
    *,
    approval_id: uuid.UUID,
    decisions: Sequence[Mapping[str, Any]],
    user: CurrentUser,
    context: OrgContext,
    request: Request | None,
) -> ApprovalRequest:
    """The decider's answer from the inbox; resumes the run (``InboxError`` otherwise)."""
    row = await db.get(ApprovalRequest, approval_id)
    if row is None or row.decider_user_id != user.id:
        raise InboxError("approval_not_found", "approval request not found", 404)
    if row.status != "pending":
        raise InboxError("approval_stale", f"the approval request is already {row.status}")
    if service.aware(row.expires_at) <= service.now():
        raise InboxError("approval_expired", "the approval request has expired")
    _check_decisions(row, decisions)
    conversation = await load_private_conversation(db, row.conversation_id, context)
    if conversation is None:  # access revoked, or another organization's request
        raise InboxError("approval_not_found", "approval request not found", 404)
    value = await _pending_value(db, conversation, context, row.interrupt_id)
    if value is None:
        await close(db, row, "no_longer_pending")
        raise InboxError("approval_stale", "the run no longer waits for this request")
    if service.args_digest(value) != row.args_digest:
        await close(db, row, "arguments_changed")
        raise InboxError("approval_stale", "the tool arguments changed since the request")
    await _respond(
        db,
        row=row,
        conversation=conversation,
        decisions=decisions,
        user=user,
        context=context,
        request=request,
        channel="inbox",
    )
    await db.refresh(row)
    return row


# --- expiry sweep and the "about to expire" notice ---------------------------------


@dataclass(frozen=True, slots=True)
class _Due:
    id: uuid.UUID
    tenant_id: uuid.UUID
    org_id: uuid.UUID | None
    subject_user_id: uuid.UUID
    conversation_id: uuid.UUID


async def _due(kind: str) -> list[_Due]:
    async with platform_session("scheduler.approval_expiry", maker=async_session) as db:
        rows = await (
            service.due_for_expiry(db) if kind == "expiry" else service.due_for_notice(db)
        )
        found = await db.execute(
            select(Conversation.id, Conversation.org_id).where(
                Conversation.id.in_({row.conversation_id for row in rows})
            )
        )
        orgs: dict[uuid.UUID, uuid.UUID | None] = {cid: org for cid, org in found.tuples()}  # noqa: C416
        return [
            _Due(
                r.id,
                r.tenant_id,
                orgs.get(r.conversation_id) or r.org_id,
                r.subject_user_id,
                r.conversation_id,
            )
            for r in rows
        ]


async def _subject(db: AsyncSession, user_id: uuid.UUID) -> CurrentUser | None:
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return CurrentUser(id=user.id, email=user.email, name=user.name)


async def _expire_one(due: _Due) -> None:
    if due.org_id is None:
        return
    context = OrgContext(org_id=due.org_id, tenant_id=due.tenant_id, user_id=due.subject_user_id)
    async with tenant_session(context, maker=async_session) as db:
        row = await db.get(ApprovalRequest, due.id)
        if row is None or row.status != "pending":
            return
        user = await _subject(db, due.subject_user_id)
        conversation: Conversation | None = None
        if user is not None:
            try:
                conversation = await load_private_conversation(db, due.conversation_id, context)
            except AppError:  # the subject lost access meanwhile: nothing to resume
                await db.rollback()
        rejections = [{"type": "reject", "message": EXPIRED_MESSAGE} for _ in row.actions]
        resumable = conversation is not None and all(
            "reject" in action.get("allowed_decisions", []) for action in row.actions
        )
        try:
            if resumable and user is not None and conversation is not None:
                # The agent hears the rejection and finishes its turn.
                await _respond(
                    db,
                    row=row,
                    conversation=conversation,
                    decisions=rejections,
                    user=user,
                    context=context,
                    request=None,
                    channel="expiry",
                )
        except (InboxError, AppError) as exc:
            logger.info("expired approval %s not resumed: %s", due.id, exc)
            await db.rollback()
        await db.refresh(row)
        if row.status == "pending":  # nothing resumed: end the request on its own
            await service.claim(
                db,
                service.Claim(
                    row.run_id,
                    {row.interrupt_id: ["reject"] * len(row.actions)},
                    "expiry",
                    None,
                ),
            )
        await notify(
            db,
            kind="approval.decided",
            recipients=[row.subject_user_id],
            tenant_id=row.tenant_id,
            org_id=row.org_id,
            target=Target("approval_request", row.id, row.agent_name),
            payload={"status": "expired", "source": row.source},
            link=INBOX_LINK,
        )
        await db.commit()


async def expire_due() -> int:
    """End every request past its expiry; returns how many were handled."""
    handled = 0
    for due in await _due("expiry"):
        try:
            await _expire_one(due)
            handled += 1
        except Exception:
            logger.exception("approval expiry failed approval_id=%s", due.id)
    return handled


async def notify_expiring() -> int:
    sent = 0
    for due in await _due("notice"):
        async with tenant_session(
            SessionTenant(due.tenant_id, due.subject_user_id), maker=async_session
        ) as db:
            row = await db.get(ApprovalRequest, due.id)
            if row is None or row.status != "pending" or row.expiring_notified_at is not None:
                continue
            row.expiring_notified_at = service.now()
            await notify(
                db,
                kind="approval.expiring",
                recipients=[row.decider_user_id],
                tenant_id=row.tenant_id,
                org_id=row.org_id,
                target=Target("approval_request", row.id, row.agent_name),
                payload={"source": row.source},
                link=INBOX_LINK,
            )
            await db.commit()
            sent += 1
    return sent


async def sweep() -> dict[str, int]:
    """The leader's minute job: notices first, then expiries."""
    return {"notified": await notify_expiring(), "expired": await expire_due()}


__all__ = [
    "EXPIRED_MESSAGE",
    "InboxError",
    "close",
    "decide",
    "expire_due",
    "notify_expiring",
    "sweep",
]
