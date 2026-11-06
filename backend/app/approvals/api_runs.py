"""An Agent API run that waits for the approval inbox (feature plan v2 순번 21, 17.3).

A deployment with ``approval_policy="inbox"`` runs the native wait endpoints
(``/v1/runs/wait``, ``/v1/threads/{id}/runs/wait``) with the standard approval interrupt:

1. The run pauses like a schedule run (``recording.pause``): it becomes ``interrupted``, its
   requests carry the API run's id and the deployment owner is notified. The API run is
   ``waiting`` and the caller gets that answer with the request ids and expiry.
2. The owner decides in the inbox (or the request expires); that resumes the run in the API
   conversation.
3. ``settle``: when the resumed run ends, the API run ends with it: ``succeeded`` with the
   run's final answer, or ``failed`` (``APPROVAL_EXPIRED`` for an expired request). A resume
   that stops for another approval keeps it waiting. The caller reads it from
   ``GET /v1/runs/{id}``.
4. ``abandon``: a request retired without a decision ends the API run ``failed``
   (``APPROVAL_CANCELED``) once none of its requests is pending.

The caller never decides: an API key is the system asking, not the person who approves.
Streams and the OpenAI/Dify compatible endpoints keep answering BLOCKED.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.triggers import WAITING, still_pending
from app.authz.org_context import OrgContext
from app.database import TenantScoped, async_session, tenant_session
from app.models.agent_api import AgentApiRun, utc_now_naive
from app.models.approval_request import ApprovalRequest

EXPIRED_CODE = "APPROVAL_EXPIRED"
CANCELED_CODE = "APPROVAL_CANCELED"
FAILED_CODE = "AGENT_API_RUN_FAILED"


@dataclass(frozen=True, slots=True)
class ApiAwaitingApproval:
    """The wait call's outcome when the run paused for the deployment owner's inbox."""

    interrupt_id: str


@dataclass(frozen=True, slots=True)
class PendingApproval:
    id: uuid.UUID
    expires_at: datetime
    tools: list[str]


async def pending_for(db: AsyncSession, api_run_id: uuid.UUID) -> list[PendingApproval]:
    rows = (
        await db.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.api_run_id == api_run_id, ApprovalRequest.status == "pending")
            .order_by(ApprovalRequest.created_at, ApprovalRequest.id)
        )
    ).scalars()
    return [
        PendingApproval(
            row.id, row.expires_at, [str(action.get("name") or "") for action in row.actions]
        )
        for row in rows
    ]


async def final_answer(
    db: AsyncSession,
    tenant: TenantScoped,
    conversation_id: uuid.UUID,
    secret_values: Iterable[str],
) -> str:
    """The conversation's last message as the wait endpoint answers it (projected, masked)."""
    from app.agent_runtime.conversation_checkpointer import conversation_checkpointer
    from app.agent_runtime.message_utils import content_to_text
    from app.agent_runtime.offload_protocol_projection import project_offload_egress_data
    from app.agent_runtime.protocol_redaction import redact_protocol_data

    saver = await conversation_checkpointer(str(conversation_id), tenant, db=db)
    found = await saver.aget_tuple({"configurable": {"thread_id": str(conversation_id)}})
    channels: dict[str, Any] = (found.checkpoint.get("channel_values") or {}) if found else {}
    messages = channels.get("messages") or []
    if not messages or not hasattr(messages[-1], "content"):
        return ""
    content = project_offload_egress_data(messages[-1].content)
    return content_to_text(
        redact_protocol_data("messages", content, secret_values=list(secret_values))
    )


async def settle(
    run_id: uuid.UUID, status: str, context: OrgContext, secret_values: Iterable[str] = ()
) -> None:
    """End the API run a resumed run answered (no-op for every other run)."""
    if status == "interrupted":
        return  # waiting again: the new requests carry the API run on
    async with tenant_session(context, maker=async_session) as db:
        row = (
            await db.execute(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.resumed_run_id == run_id,
                    ApprovalRequest.api_run_id.is_not(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None or row.api_run_id is None:
            return
        api_run = await db.get(AgentApiRun, row.api_run_id, with_for_update=True)
        if api_run is None or api_run.status != WAITING:
            return
        if row.status == "expired":
            _fail(api_run, EXPIRED_CODE, "the approval request expired")
        elif status == "completed":
            api_run.status = "succeeded"
            answer = await final_answer(db, context, row.conversation_id, secret_values)
            api_run.output = {"answer": answer}
            api_run.finished_at = utc_now_naive()
        else:
            _fail(api_run, FAILED_CODE, f"the resumed run ended {status}")
        await db.commit()


async def abandon(tenant: TenantScoped, request_ids: Iterable[uuid.UUID]) -> None:
    """End the waiting API runs whose requests were all retired without a decision."""
    async with tenant_session(tenant, maker=async_session) as db:
        rows = (
            await db.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.id.in_(list(request_ids)),
                    ApprovalRequest.api_run_id.is_not(None),
                    ApprovalRequest.status == "canceled",
                )
            )
        ).scalars()
        for row in {r.api_run_id: r for r in rows}.values():
            if await still_pending(db, ApprovalRequest.api_run_id == row.api_run_id):
                continue
            api_run = await db.get(AgentApiRun, row.api_run_id, with_for_update=True)
            if api_run is None or api_run.status != WAITING:
                continue
            _fail(api_run, CANCELED_CODE, f"the approval request was {row.reason or 'canceled'}")
        await db.commit()


def _fail(api_run: AgentApiRun, code: str, message: str) -> None:
    api_run.status = "failed"
    api_run.error_code = code
    api_run.error_message = message
    api_run.finished_at = utc_now_naive()


__all__ = [
    "CANCELED_CODE",
    "EXPIRED_CODE",
    "ApiAwaitingApproval",
    "PendingApproval",
    "abandon",
    "final_answer",
    "pending_for",
    "settle",
]
