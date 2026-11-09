"""Workflow approval nodes in the approval inbox (workflow-plan 3.4, W2b).

An approval node stops the run (``waiting``) and leaves one ``business`` approval request
for the runner (1차 Q5: the runner decides). The request has no conversation run: it points
at the waiting step. Its decision moves away from ``pending`` exactly once (the same
compare-and-set as tool approvals), records the step's branch (``approved``/``rejected``)
and queues the run again. Expiry takes the rejected branch (plan 3.2); canceling the run
cancels its pending requests.

An agent step whose result is unknown (3.5: accepted, never stored) asks the runner the
same way (W4): approving runs the step again in a fresh conversation (the runner checked
the earlier one did nothing that must not repeat), rejecting or letting it expire ends the
run as failed. The step is never run again without that answer.

An ``ask`` node (workflow-nodes-plan N5) asks the runner a question the same way: the only
decision is ``approve`` with the written answer as its message (the ``answered`` branch);
expiry takes the ``timeout`` branch.
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
from app.workflows.definition import ApprovalNode, AskNode
from app.workflows.values import render

BUSINESS: Final = "business"
ACTION_NAME: Final = "workflow_approval"
UNKNOWN_ACTION: Final = "workflow_unknown_check"
ASK_ACTION: Final = "workflow_ask"
DECISIONS: Final = ("approve", "reject")
MAX_ANSWER: Final = 2_000  # the inbox decision message limit


class AnswerRequired(ValueError):
    """An ``ask`` request decided without a written answer."""


# Long enough to look at the conversation; unanswered, the run ends as failed.
UNKNOWN_CHECK_MINUTES: Final = 7 * 24 * 60


def _interrupt_id(step: WorkflowStepRun) -> str:
    return f"workflow:{step.id}"


async def may_run(workflow: Workflow, user_id: uuid.UUID) -> bool:
    """Whether ``user_id`` may run the workflow (a designated approver must, plan 3.4)."""
    from app.authz.conversation_agent_checks import current_authz
    from app.authz.principal import Principal

    if user_id == workflow.user_id:
        return True
    authz = current_authz()
    if not authz.enforces("workflow"):
        return False  # legacy: only the owner can see the workflow
    context = OrgContext(org_id=workflow.org_id, tenant_id=workflow.tenant_id, user_id=user_id)
    return await authz.check(Principal.for_user(context), "can_run", f"workflow:{workflow.id}")


async def _decider(
    db: AsyncSession, run: WorkflowRun, node: ApprovalNode
) -> tuple[uuid.UUID, str | None]:
    """The approver; the runner instead (with the reason) when the named one lost access."""
    approver = node.approver
    if approver is None or approver.kind == "runner":
        return run.user_id, None
    workflow = await db.get(Workflow, run.workflow_id)
    if workflow is None:
        return run.user_id, "workflow_gone"
    target = workflow.user_id if approver.kind == "owner" else uuid.UUID(str(approver.user_id))
    if target == run.user_id or await may_run(workflow, target):
        return target, None
    return run.user_id, "approver_no_access"


async def request(
    db: AsyncSession,
    run: WorkflowRun,
    step: WorkflowStepRun,
    node: ApprovalNode,
    values: dict[str, Any],
) -> ApprovalRequest:
    title = render(node.title, values)[:500]
    body = render(node.body, values)[:4000]
    decider, fallback = await _decider(db, run, node)
    args: dict[str, Any] = {"title": title, "body": body, "node_id": node.id}
    if fallback is not None:
        args["approver_fallback"] = fallback
    return await _business_request(
        db,
        run,
        step,
        ACTION_NAME,
        args,
        _interrupt_id(step),
        node.timeout_minutes or 1440,
        decider=decider,
    )


async def request_ask(
    db: AsyncSession,
    run: WorkflowRun,
    step: WorkflowStepRun,
    node: AskNode,
    values: dict[str, Any],
) -> ApprovalRequest:
    question = render(node.question, values)[:4000]
    args = {"title": (node.label or "질문")[:500], "body": question, "node_id": node.id}
    return await _business_request(
        db,
        run,
        step,
        ASK_ACTION,
        args,
        _interrupt_id(step),
        node.timeout_minutes or 1440,
        decisions=("approve",),
    )


async def request_unknown_check(
    db: AsyncSession, run: WorkflowRun, step: WorkflowStepRun, label: str
) -> ApprovalRequest:
    """Ask the runner what to do with a step whose result is unknown (3.5, W4)."""
    attempts = len((step.input or {}).get("previous_attempts", []))
    args = {
        "title": f"결과를 알 수 없는 단계: {label}"[:500],
        "body": (
            "에이전트가 실행 도중 멈춰 무엇을 했는지 알 수 없습니다. 대화 기록을 확인하고, "
            "다시 실행해도 되면 승인하세요. 거절하면 실행을 실패로 끝냅니다."
        ),
        "node_id": step.node_id,
        "conversation_id": str(step.conversation_id) if step.conversation_id else None,
    }
    interrupt = f"workflow-unknown:{step.id}:{attempts}"
    return await _business_request(
        db, run, step, UNKNOWN_ACTION, args, interrupt, UNKNOWN_CHECK_MINUTES
    )


async def _business_request(
    db: AsyncSession,
    run: WorkflowRun,
    step: WorkflowStepRun,
    name: str,
    args: dict[str, Any],
    interrupt_id: str,
    minutes: int,
    *,
    decisions: tuple[str, ...] = DECISIONS,
    decider: uuid.UUID | None = None,
) -> ApprovalRequest:
    workflow = await db.get(Workflow, run.workflow_id)
    actions = [
        {
            "name": name,
            "args": args,
            "allowed_decisions": list(decisions),
            "description": args["title"],
        }
    ]
    row = ApprovalRequest(
        tenant_id=run.tenant_id,
        org_id=run.org_id,
        conversation_id=None,
        run_id=None,
        interrupt_id=interrupt_id,
        namespace=[],
        kind=BUSINESS,
        workflow_run_id=run.id,
        workflow_step_run_id=step.id,
        agent_id=None,
        agent_name=(workflow.name if workflow else "워크플로")[:200],
        source="workflow",
        subject_user_id=run.user_id,
        requester_user_id=run.user_id,
        # The runner unless the approval node names someone else (plan 3.4).
        decider_user_id=decider or run.user_id,
        actions=actions,
        args_digest=service.digest(actions),
        expires_at=service.now() + timedelta(minutes=minutes),
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
    if step is None or run is None:
        return
    action = row.actions[0].get("name") if row.actions else None
    if action == UNKNOWN_ACTION:
        await _settle_unknown(db, row, run, step, approved=decision == "approve")
        return
    if step.status != "waiting":
        return
    approved = decision == "approve"
    if action == ASK_ACTION:
        answered = approved and row.status != "expired"
        step.branch = "answered" if answered else "timeout"
        step.output = {"answer": comment if answered else None}
    else:
        step.branch = "approved" if approved else "rejected"
        step.output = {
            "decision": "approved"
            if approved
            else ("expired" if row.status == "expired" else "rejected"),
            "comment": comment,
        }
    step.status = "completed"
    step.finished_at = service.now().replace(tzinfo=None)
    if run.status == "waiting":
        run.status = "running"
        await _resume(db, run, row)
    await db.flush()


async def _settle_unknown(
    db: AsyncSession,
    row: ApprovalRequest,
    run: WorkflowRun,
    step: WorkflowStepRun,
    *,
    approved: bool,
) -> None:
    if step.status != "unknown" or run.status != "unknown":
        return
    if not approved:
        from app.workflows.runner import fail_unknown  # runner imports this module

        await fail_unknown(db, run)
        return
    # Run it again: a fresh conversation and acceptance; the earlier one stays on record.
    earlier = {
        "conversation_id": str(step.conversation_id) if step.conversation_id else None,
        "conversation_run_id": str(step.conversation_run_id) if step.conversation_run_id else None,
        "approval_request_id": str(row.id),
    }
    step.input = {
        **(step.input or {}),
        "previous_attempts": [*(step.input or {}).get("previous_attempts", []), earlier],
    }
    step.status, step.output, step.error_code, step.error_message = "queued", None, None, None
    step.conversation_id = step.conversation_run_id = step.finished_at = None
    step.started_at = service.now().replace(tzinfo=None)
    if step.parent_step_id is not None:
        # A child of a for_each/parallel step: its parent runs the rest of its children.
        parent = await db.get(WorkflowStepRun, step.parent_step_id, with_for_update=True)
        if parent is not None and parent.status == "unknown":
            parent.status, parent.error_code, parent.error_message = "running", None, None
            parent.finished_at = None
    run.status, run.error_code, run.error_message = "running", None, None
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
    allowed = (row.actions[0].get("allowed_decisions") if row.actions else None) or DECISIONS
    if decision not in DECISIONS or decision not in allowed:
        raise ValueError(decision)
    if row.actions and row.actions[0].get("name") == ASK_ACTION:
        comment = (comment or "").strip()[:MAX_ANSWER]
        if not comment:
            raise AnswerRequired("an answer is required")
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


__all__ = [
    "ASK_ACTION",
    "AnswerRequired",
    "BUSINESS",
    "UNKNOWN_ACTION",
    "cancel_for_run",
    "decide",
    "expire",
    "pending_for_run",
    "request",
    "request_ask",
    "request_unknown_check",
    "settle",
]
