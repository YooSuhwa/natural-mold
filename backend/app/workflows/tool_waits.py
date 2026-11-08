"""Tool approvals inside a workflow agent step (workflow-plan 3.4, W2c).

A step runs with ``unattended_approval="wait"``. When its agent stops before a tool that
needs a person, the step and the run become ``waiting`` and the requests carry the step
(``RunOrigin.workflow_*``; a later wait in the chain inherits them).

The decision resumes through the standard chat resume path (``input.respond``), so the
edit name-fill, redacted argument restore and one-decision rules are the same as for every
other run. Two things are workflow specific:

* ``adapt_resume``: the resumed run gets the node's output schema back and keeps waiting
  for the inbox (the chat resume path rebuilds the config from the agent alone).
* ``settle``: when the resumed run ends, the step records the checked answer (read from
  the response tool call in the checkpoint) and the run continues.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.memory_paths import origin_run
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.structured_output import structured_outcome
from app.authz.org_context import OrgContext
from app.database import TenantScoped, async_session, tenant_session
from app.jobs.service import EnqueueRequest, enqueue
from app.models.approval_request import ApprovalRequest
from app.models.conversation_run import ConversationRun
from app.models.workflow import WorkflowRun, WorkflowStepRun, WorkflowVersion
from app.workflows.definition import AgentNode, WorkflowDefinition

logger = logging.getLogger(__name__)


async def _step_for(db: AsyncSession, run: ConversationRun) -> WorkflowStepRun | None:
    first = await origin_run(db, run)
    if first is None:
        return None
    return await db.scalar(
        select(WorkflowStepRun).where(WorkflowStepRun.conversation_run_id == first.id)
    )


async def _node(db: AsyncSession, step: WorkflowStepRun) -> AgentNode | None:
    run = await db.get(WorkflowRun, step.run_id)
    version = await db.get(WorkflowVersion, run.version_id) if run else None
    if version is None:
        return None
    definition = WorkflowDefinition.model_validate(version.definition)
    node = next((n for n in definition.nodes if n.id == step.node_id), None)
    return node if isinstance(node, AgentNode) else None


async def adapt_resume(db: AsyncSession, parent_run: ConversationRun, cfg: AgentConfig) -> None:
    """A resumed workflow step answers in its node's schema and still waits for the inbox."""
    step = await _step_for(db, parent_run)
    if step is None:
        return
    node = await _node(db, step)
    if node is None:
        return
    cfg.output_schema = node.output_schema
    cfg.unattended_approval = "wait"


async def settle(
    run_id: uuid.UUID, status: str, context: OrgContext, secret_values: Iterable[str] = ()
) -> None:
    """The resumed run of a waiting step ended: store its answer and continue the run."""
    if status == "interrupted":
        return  # waiting again: the new requests carry the step on
    async with tenant_session(context, maker=async_session) as db:
        resumed = await db.get(ConversationRun, run_id)
        if resumed is None:
            return
        step = await _step_for(db, resumed)
        if step is None or step.status != "waiting":
            return
        step = await db.get(WorkflowStepRun, step.id, with_for_update=True)
        run = await db.get(WorkflowRun, step.run_id, with_for_update=True) if step else None
        node = await _node(db, step) if step else None
        if step is None or run is None or node is None or step.status != "waiting":
            return
        answer = await _answer(db, step, node, context) if status == "completed" else None
        _record(step, status, answer, secret_values)
        await _continue(db, run, context, f"{run.id}:{run_id}")
        await db.commit()


async def _continue(db: AsyncSession, run: WorkflowRun, context: OrgContext, key: str) -> None:
    if run.status == "waiting":
        run.status = "running"
        await enqueue(
            db,
            context,
            EnqueueRequest(
                type="workflow.run",
                payload={"run_id": str(run.id)},
                resource_id=str(run.id),
                idempotency_key=key,
                max_attempts=3,
            ),
            actor_id=run.user_id,
        )


async def _answer(
    db: AsyncSession, step: WorkflowStepRun, node: AgentNode, context: OrgContext
) -> Any:
    from app.agent_runtime.conversation_checkpointer import conversation_checkpointer

    if step.conversation_id is None:
        return None
    thread = str(step.conversation_id)
    saver = await conversation_checkpointer(thread, context, db=db)
    found = await saver.aget_tuple({"configurable": {"thread_id": thread}})
    channels: dict[str, Any] = (found.checkpoint.get("channel_values") or {}) if found else {}
    return structured_outcome(channels, node.output_schema)


def _record(step: WorkflowStepRun, status: str, outcome: Any, secret_values: Iterable[str]) -> None:
    from datetime import UTC, datetime

    from app.agent_runtime.protocol_redaction import redact_protocol_data

    step.finished_at = datetime.now(UTC).replace(tzinfo=None)
    if status == "completed" and outcome is not None and outcome.status == "ok":
        step.status = "completed"
        step.output = redact_protocol_data(
            "messages", outcome.value, secret_values=list(secret_values)
        )
    elif status == "completed":
        reason = outcome.status if outcome is not None else "schema_invalid"
        step.status, step.error_code = "failed", f"output_{reason}"
        step.error_message = "에이전트 답이 출력 형식에 맞지 않습니다"
    elif status == "canceled":
        step.status, step.error_code = "canceled", "canceled"
    else:
        step.status, step.error_code = "failed", "agent_failed"
        step.error_message = "에이전트 실행 실패"


async def abandon(tenant: TenantScoped, request_ids: Iterable[uuid.UUID]) -> None:
    """A waiting step whose requests were all retired without a decision fails."""
    async with tenant_session(tenant, maker=async_session) as db:
        rows = list(
            (
                await db.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.id.in_(list(request_ids)),
                        ApprovalRequest.kind == "tool",
                        ApprovalRequest.workflow_step_run_id.is_not(None),
                        ApprovalRequest.status == "canceled",
                    )
                )
            ).scalars()
        )
        for step_id, row in {r.workflow_step_run_id: r for r in rows}.items():
            pending = await db.scalar(
                select(ApprovalRequest.id)
                .where(
                    ApprovalRequest.workflow_step_run_id == step_id,
                    ApprovalRequest.status == "pending",
                )
                .limit(1)
            )
            step = await db.get(WorkflowStepRun, step_id, with_for_update=True)
            if pending is not None or step is None or step.status != "waiting":
                continue
            run = await db.get(WorkflowRun, step.run_id, with_for_update=True)
            if run is None:
                continue
            step.status, step.error_code = "failed", (row.reason or "approval_canceled")[:80]
            step.error_message = "승인 요청이 결정 없이 끝났습니다"
            context = OrgContext(org_id=run.org_id, tenant_id=run.tenant_id, user_id=run.user_id)
            await _continue(db, run, context, f"{run.id}:{row.id}:abandoned")
        await db.commit()


async def settle_best_effort(
    run_id: uuid.UUID, status: str, context: OrgContext, secret_values: Iterable[str] = ()
) -> None:
    try:
        await settle(run_id, status, context, secret_values)
    except Exception:
        logger.exception("workflow tool wait settle failed run_id=%s", run_id)


async def abandon_best_effort(tenant: TenantScoped, request_ids: Iterable[uuid.UUID]) -> None:
    try:
        await abandon(tenant, request_ids)
    except Exception:
        logger.exception("workflow tool wait abandon failed")


__all__ = ["abandon", "abandon_best_effort", "adapt_resume", "settle", "settle_best_effort"]
