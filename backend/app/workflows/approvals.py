"""Workflow approval nodes in the approval inbox (workflow-plan 3.4, W2b).

An approval node stops the run (``waiting``) and leaves one ``business`` approval request
for the runner (1차 Q5: the runner decides). The request has no conversation run: it points
at the waiting step. Its decision moves away from ``pending`` exactly once (the same
compare-and-set as tool approvals), records the step's branch (``approved``/``rejected``)
and queues the run again. Expiry takes the rejected branch (plan 3.2); canceling the run
cancels its pending requests.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals import service
from app.approvals.recording import notify_requested
from app.authz.org_context import OrgContext
from app.jobs.service import EnqueueRequest, enqueue
from app.models.approval_request import ApprovalRequest
from app.models.workflow import Workflow, WorkflowRun, WorkflowStepRun
from app.workflows.definition import ApprovalNode
from app.workflows.values import render

BUSINESS: Final = "business"
ACTION_NAME: Final = "workflow_approval"
DECISIONS: Final = ("approve", "reject")


def _interrupt_id(step: WorkflowStepRun) -> str:
    return f"workflow:{step.id}"


async def request(
    db: AsyncSession,
    run: WorkflowRun,
    step: WorkflowStepRun,
    node: ApprovalNode,
    values: dict[str, Any],
) -> ApprovalRequest:
    workflow = await db.get(Workflow, run.workflow_id)
    title = render(node.title, values)[:500]
    body = render(node.body, values)[:4000]
    actions = [
        {
            "name": ACTION_NAME,
            "args": {"title": title, "body": body, "node_id": node.id},
            "allowed_decisions": list(DECISIONS),
            "description": title,
        }
    ]
    row = ApprovalRequest(
        tenant_id=run.tenant_id,
        org_id=run.org_id,
        conversation_id=None,
        run_id=None,
        interrupt_id=_interrupt_id(step),
        namespace=[],
        kind=BUSINESS,
        workflow_run_id=run.id,
        workflow_step_run_id=step.id,
        agent_id=None,
        agent_name=(workflow.name if workflow else "워크플로")[:200],
        source="workflow",
        subject_user_id=run.user_id,
        requester_user_id=run.user_id,
        decider_user_id=run.user_id,
        actions=actions,
        args_digest=service.digest(actions),
        expires_at=service.now() + timedelta(minutes=node.timeout_minutes or 1440),
    )
    db.add(row)
    await db.flush()
    await service.audit_requested(db, row)
    await notify_requested(db, [row])
    return row


async def _resume(db: AsyncSession, run: WorkflowRun, row: ApprovalRequest) -> None:
    context = OrgContext(org_id=run.org_id, tenant_id=run.tenant_id, user_id=run.user_id)
    await enqueue(
        db,
        context,
        EnqueueRequest(
            type="workflow.run",
            payload={"run_id": str(run.id)},
            resource_id=str(run.id),
            idempotency_key=f"{run.id}:{row.id}",
            max_attempts=3,
        ),
        actor_id=run.user_id,
    )


async def settle(
    db: AsyncSession, row: ApprovalRequest, *, decision: str, comment: str | None
) -> None:
    """After the compare-and-set: give the step its branch and continue the run."""
    step = await db.get(WorkflowStepRun, row.workflow_step_run_id, with_for_update=True)
    run = await db.get(WorkflowRun, row.workflow_run_id, with_for_update=True)
    if step is None or run is None or step.status != "waiting":
        return
    approved = decision == "approve"
    step.status = "completed"
    step.branch = "approved" if approved else "rejected"
    step.output = {
        "decision": "approved"
        if approved
        else ("expired" if row.status == "expired" else "rejected"),
        "comment": comment,
    }
    step.finished_at = service.now().replace(tzinfo=None)
    if run.status == "waiting":
        run.status = "running"
        await _resume(db, run, row)
    await db.flush()


async def decide(
    db: AsyncSession,
    row: ApprovalRequest,
    *,
    decision: str,
    comment: str | None,
    actor_id: uuid.UUID,
) -> ApprovalRequest:
    """The runner's answer (inbox). ``service.ApprovalStale`` when already decided."""
    if decision not in DECISIONS:
        raise ValueError(decision)
    await service.claim_business(db, row, decision=decision, channel="inbox", actor_id=actor_id)
    await settle(db, row, decision=decision, comment=comment)
    return row


async def expire(db: AsyncSession, row: ApprovalRequest) -> None:
    """Past its time limit: the request expires and the run takes the rejected branch."""
    await service.claim_business(db, row, decision="reject", channel="expiry", actor_id=None)
    await settle(db, row, decision="reject", comment=None)


async def cancel_for_run(db: AsyncSession, run: WorkflowRun) -> list[uuid.UUID]:
    result = await db.execute(
        update(ApprovalRequest)
        .where(ApprovalRequest.workflow_run_id == run.id, ApprovalRequest.status == "pending")
        .values(status="canceled", reason="workflow_canceled", decided_at=service.now())
        .returning(ApprovalRequest.id)
        .execution_options(synchronize_session=False)
    )
    return list(result.scalars())


async def pending_for_run(db: AsyncSession, run_id: uuid.UUID) -> list[ApprovalRequest]:
    rows = await db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.workflow_run_id == run_id, ApprovalRequest.status == "pending"
        )
    )
    return list(rows.scalars())


__all__ = ["BUSINESS", "cancel_for_run", "decide", "expire", "pending_for_run", "request", "settle"]
