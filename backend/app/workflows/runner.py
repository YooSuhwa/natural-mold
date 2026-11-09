"""Starting, running and canceling workflow runs (workflow-plan 3.4-3.5, W2a).

A run walks its published version one node at a time. There are no parallel branches,
so a run is a single chain: the next node is the successor of the last finished step along
the branch it took. A loop (workflow-loop-plan) is a decision exit that leads back: the
steps it reaches run again as a new **round** (a new row each; earlier rounds are kept), and
once the decision has run ``repeat.max_rounds`` times (or the usage limit is reached) it
takes its ``limit`` exit instead. The step rows are the only progress record (no separate
graph checkpoint): a reclaimed job reads them and continues, finished steps are never run
again, and an agent step that was accepted but never stored is ``unknown`` (3.5).

The ``workflow.run`` job holds one run. Each node commits on its own, so a crash loses at
most the node in flight.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import audit_actions
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scoped
from app.exceptions import AppError, ConflictError, NotFoundError
from app.jobs import registry
from app.jobs.context import JobContext
from app.jobs.service import EnqueueRequest, enqueue
from app.models.conversation_run import ConversationRun
from app.models.workflow import (
    RUN_TERMINAL,
    Workflow,
    WorkflowRun,
    WorkflowStepRun,
    WorkflowVersion,
)
from app.services import audit_service
from app.services.conversation_run_state import request_cancel_run
from app.workflows import agent_step
from app.workflows import approvals as workflow_approvals
from app.workflows.checks import loop_shape
from app.workflows.definition import (
    AgentNode,
    ApprovalNode,
    DecisionNode,
    FailNode,
    Node,
    OutputNode,
    StartNode,
    TransformNode,
    WorkflowDefinition,
    WorkflowInput,
)
from app.workflows.pins import agent_fingerprints
from app.workflows.service import agent_issues, agent_node_issues, get_workflow, parse_definition
from app.workflows.values import agent_prompt, evaluate, field_value, render

logger = logging.getLogger(__name__)

RUN_JOB: Final = "workflow.run"
RUN_STARTED: Final = audit_actions.WORKFLOW_RUN_START
RUN_FINISHED: Final = audit_actions.WORKFLOW_RUN_FINISH
RUN_CANCELED: Final = audit_actions.WORKFLOW_RUN_CANCEL
STEP_UNKNOWN: Final = audit_actions.WORKFLOW_STEP_UNKNOWN
LOOP_LIMIT: Final = audit_actions.WORKFLOW_LOOP_LIMIT


def run_not_found() -> NotFoundError:
    return NotFoundError("WORKFLOW_RUN_NOT_FOUND", "워크플로 실행을 찾을 수 없습니다")


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _input_problems(inputs: list[WorkflowInput], raw: dict[str, Any]) -> list[dict[str, str]]:
    declared = {item.name: item for item in inputs}
    problems = [
        {"name": name, "message": "선언하지 않은 입력입니다"}
        for name in raw
        if name not in declared
    ]
    for item in inputs:
        value = raw.get(item.name)
        if value is None:
            if item.required:
                problems.append({"name": item.name, "message": "필수 입력입니다"})
            continue
        expected = {"string": str, "number": int | float, "integer": int, "boolean": bool}
        wrong = isinstance(value, bool) and item.type != "boolean"
        if wrong or not isinstance(value, expected[item.type]):
            problems.append({"name": item.name, "message": f"{item.type} 값이어야 합니다"})
        elif (
            item.max_length is not None and isinstance(value, str) and len(value) > item.max_length
        ):
            problems.append({"name": item.name, "message": f"{item.max_length}자를 넘습니다"})
    return problems


async def start_run(
    db: AsyncSession, context: OrgContext, workflow_id: uuid.UUID, inputs: dict[str, Any]
) -> WorkflowRun:
    """Check and queue a run of the published version (committed by the caller)."""
    workflow = await get_workflow(db, context, workflow_id)
    if workflow.archived_at is not None:
        raise ConflictError("WORKFLOW_ARCHIVED", "보관한 워크플로는 실행할 수 없습니다")
    if workflow.published_version_no is None:
        raise ConflictError("WORKFLOW_NOT_PUBLISHED", "게시한 버전이 없습니다")
    version = await db.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow.id,
            WorkflowVersion.version_no == workflow.published_version_no,
        )
    )
    if version is None:
        raise ConflictError("WORKFLOW_NOT_PUBLISHED", "게시한 버전이 없습니다")
    definition = parse_definition(version.definition)
    problems = _input_problems(definition.inputs, inputs)
    if problems:
        raise AppError(
            "WORKFLOW_INPUT_INVALID",
            "입력값을 확인하세요",
            status=422,
            details={"problems": problems},
        )
    await _check_agents(db, context, definition, version)
    run = WorkflowRun(
        workflow_id=workflow.id,
        version_id=version.id,
        user_id=context.user_id,
        org_id=workflow.org_id,
        tenant_id=workflow.tenant_id,
        status="queued",
        input=dict(inputs),
    )
    db.add(run)
    await db.flush()
    job = await enqueue(
        db,
        context,
        EnqueueRequest(
            type=RUN_JOB,
            payload={"run_id": str(run.id)},
            resource_id=str(run.id),
            idempotency_key=str(run.id),
            max_attempts=3,
        ),
        actor_id=context.user_id,
    )
    run.job_id = job.id
    await db.flush()
    return run


async def _check_agents(
    db: AsyncSession, context: OrgContext, definition: WorkflowDefinition, version: Any
) -> None:
    """Q1: the agents still exist for the runner and still run the published settings."""
    issues = await agent_issues(db, context, definition)
    if issues:
        raise ConflictError("WORKFLOW_AGENT_UNAVAILABLE", issues[0].message)
    pins = version.agent_pins or {}
    live = await agent_fingerprints(db, [uuid.UUID(pin["agent_id"]) for pin in pins.values()])
    changed = sorted(
        node_id
        for node_id, pin in pins.items()
        if live.get(uuid.UUID(pin["agent_id"])) != pin["fingerprint"]
    )
    if changed:
        error = ConflictError(
            "WORKFLOW_AGENT_CHANGED", "게시한 뒤 에이전트 설정이 바뀌었습니다. 다시 게시하세요"
        )
        error.details = {"node_ids": changed}
        raise error


async def get_run(
    db: AsyncSession, context: OrgContext, run_id: uuid.UUID, *, for_update: bool = False
) -> WorkflowRun:
    stmt = private_scoped(select(WorkflowRun).where(WorkflowRun.id == run_id), WorkflowRun, context)
    if for_update:
        stmt = stmt.with_for_update()
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise run_not_found()
    return run


async def list_runs(
    db: AsyncSession, context: OrgContext, workflow_id: uuid.UUID, *, limit: int = 50
) -> list[WorkflowRun]:
    workflow = await get_workflow(db, context, workflow_id)
    stmt = (
        private_scoped(select(WorkflowRun), WorkflowRun, context)
        .where(WorkflowRun.workflow_id == workflow.id)
        .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def version_numbers(db: AsyncSession, runs: list[WorkflowRun]) -> dict[uuid.UUID, int]:
    """``{version_id: version_no}`` for the runs (one query)."""
    ids = {run.version_id for run in runs}
    if not ids:
        return {}
    rows = await db.execute(
        select(WorkflowVersion.id, WorkflowVersion.version_no).where(WorkflowVersion.id.in_(ids))
    )
    return {row.id: row.version_no for row in rows}


async def list_steps(db: AsyncSession, run: WorkflowRun) -> list[WorkflowStepRun]:
    stmt = (
        select(WorkflowStepRun)
        .where(WorkflowStepRun.run_id == run.id)
        .order_by(WorkflowStepRun.position)
    )
    return list((await db.execute(stmt)).scalars().all())


async def cancel_run(db: AsyncSession, context: OrgContext, run_id: uuid.UUID) -> WorkflowRun:
    """Stop at the next node; an agent step in flight gets the run's normal stop."""
    run = await get_run(db, context, run_id, for_update=True)
    if run.status in RUN_TERMINAL:
        return run
    run.cancel_requested = True
    await workflow_approvals.cancel_for_run(db, run)
    # Nothing is in flight while it waits for a decision (or for the unknown-step check).
    if run.status in ("queued", "waiting", "unknown"):
        for step in await list_steps(db, run):
            if step.status == "waiting":
                step.status, step.finished_at = "canceled", _now()
        _finish(run, "canceled", "canceled", "실행을 취소했습니다")
    else:
        run.status = "canceling"
        for step in await list_steps(db, run):
            if step.conversation_run_id is None or step.status not in ("queued", "running"):
                continue
            conversation_run = await db.get(
                ConversationRun, step.conversation_run_id, with_for_update=True
            )
            if conversation_run is not None and conversation_run.status in ("queued", "running"):
                await request_cancel_run(db, conversation_run)
    await db.flush()
    return run


def _finish(run: WorkflowRun, status: str, code: str | None, message: str | None) -> None:
    run.status = status
    run.error_code = code
    run.error_message = message
    run.current_node = None
    run.finished_at = _now()


# --- the job ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Next:
    node: Node | None  # None: the run is finished or must stop
    reason: str | None = None


def _successor(definition: WorkflowDefinition, step: WorkflowStepRun) -> Node | None:
    nodes = {node.id: node for node in definition.nodes}
    for edge in definition.edges:
        if edge.from_ == step.node_id and edge.when == step.branch:
            return nodes.get(edge.to)
    return None


def _values(run: WorkflowRun, steps: list[WorkflowStepRun]) -> dict[str, Any]:
    values: dict[str, Any] = {"input": run.input}
    for step in steps:
        if step.status == "completed":
            values[step.node_id] = step.output
    return values


async def _audit_finish(db: AsyncSession, run: WorkflowRun) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=run.user_id,
        owner_user_id=run.user_id,
        action=RUN_FINISHED,
        target_type="workflow_run",
        target_id=run.id,
        target_owner_user_id=run.user_id,
        outcome="success" if run.status == "completed" else "failure",
        reason_code=run.error_code,
        metadata={"workflow_id": str(run.workflow_id), "status": run.status},
    )


async def fail_unknown(db: AsyncSession, run: WorkflowRun) -> None:
    """The runner chose not to run an unknown step again (or never answered): failed."""
    _finish(
        run, "failed", "step_outcome_unknown", "결과를 알 수 없는 단계를 다시 실행하지 않았습니다"
    )
    await _audit_finish(db, run)


async def _ask_about_unknown(
    db: AsyncSession, run: WorkflowRun, step: WorkflowStepRun, node: Node | None
) -> None:
    label = (node.label if node else None) or step.node_id
    request = await workflow_approvals.request_unknown_check(db, run, step, label)
    await audit_service.record_event(
        db,
        actor_type="system",
        actor_user_id=None,
        owner_user_id=run.user_id,
        action=STEP_UNKNOWN,
        target_type="workflow_run",
        target_id=run.id,
        target_owner_user_id=run.user_id,
        outcome="failure",
        reason_code=step.error_code,
        metadata={
            "workflow_id": str(run.workflow_id),
            "step_id": str(step.id),
            "node_id": step.node_id,
            "conversation_run_id": str(step.conversation_run_id)
            if step.conversation_run_id
            else None,
            "approval_request_id": str(request.id),
        },
    )


async def _step_row(
    db: AsyncSession, run: WorkflowRun, node: Node, position: int, **values: Any
) -> WorkflowStepRun:
    step = WorkflowStepRun(
        run_id=run.id,
        user_id=run.user_id,
        org_id=run.org_id,
        tenant_id=run.tenant_id,
        position=position,
        node_id=node.id,
        node_type=node.type,
        started_at=_now(),
        **values,
    )
    db.add(step)
    await db.flush()
    return step


async def _advance(
    db: AsyncSession, run: WorkflowRun, context: OrgContext | None = None
) -> tuple[WorkflowStepRun | None, Node | None]:
    """Settle the run's state; return an agent step to execute outside this transaction."""
    version = await db.get(WorkflowVersion, run.version_id)
    if version is None:
        _finish(run, "failed", "version_gone", "게시 버전이 없습니다")
        return None, None
    definition = WorkflowDefinition.model_validate(version.definition)
    steps = await list_steps(db, run)
    last = steps[-1] if steps else None
    if last is not None and last.status != "completed":
        if last.node_type == "agent" and last.status in ("queued", "running"):
            recovered = await agent_step.recover(db, last)
            if recovered is None:
                node = next(n for n in definition.nodes if n.id == last.node_id)
                return last, node  # accepted never happened: safe to accept now
            _record(last, recovered)
            _end_from_step(run, last)
            if last.status == "unknown":
                node = next((n for n in definition.nodes if n.id == last.node_id), None)
                await _ask_about_unknown(db, run, last, node)
            return None, None
        _end_from_step(run, last)
        return None, None
    if run.cancel_requested:
        _finish(run, "canceled", "canceled", "실행을 취소했습니다")
        return None, None
    if last is None:
        node: Node | None = next((n for n in definition.nodes if isinstance(n, StartNode)), None)
    else:
        node = _successor(definition, last)
    if node is None:
        _finish(run, "failed", "no_next_node", "다음 노드가 없습니다")
        return None, None
    run.current_node = node.id
    values = _values(run, steps)
    position = len(steps)
    round_no = 1 + sum(1 for step in steps if step.node_id == node.id)
    if isinstance(node, AgentNode):
        step = await _step_row(
            db,
            run,
            node,
            position,
            status="queued",
            agent_id=uuid.UUID(node.agent_id),
            round=round_no,
        )
        step.input = {"prompt": agent_prompt(node.prompt, values)}
        return step, node
    if isinstance(node, DecisionNode):
        await _decision(db, run, definition, node, position, round_no, values, context)
        return None, None
    await _simple_node(db, run, node, position, values, round_no)
    return None, None


async def _decision(
    db: AsyncSession,
    run: WorkflowRun,
    definition: WorkflowDefinition,
    node: DecisionNode,
    position: int,
    round_no: int,
    values: dict[str, Any],
    context: OrgContext | None,
) -> None:
    """Evaluate the rule. An exit that loops back is taken while rounds and usage are left;
    otherwise the ``limit`` exit is, with the reason on the step and in the audit."""
    decision = evaluate(node.condition, values)
    branch = "true" if decision["value"] else "false"
    loops = {(loop.decision, loop.when) for loop in loop_shape(definition).loops}
    if (node.id, branch) in loops and node.repeat is not None:
        reason = None
        if round_no >= node.repeat.max_rounds:
            reason = "max_rounds"
        elif context is not None and await _usage_limited(db, context):
            reason = "usage_limit"
        if reason is not None:
            decision = {**decision, "limit_reached": True, "reason": reason}
            branch = "limit"
            await _audit_loop_limit(db, run, node, round_no, reason)
        decision = {**decision, "round": round_no, "max_rounds": node.repeat.max_rounds}
    await _step_row(
        db,
        run,
        node,
        position,
        output=decision,
        branch=branch,
        round=round_no,
        status="completed",
        finished_at=_now(),
    )


async def _usage_limited(db: AsyncSession, context: OrgContext) -> bool:
    from app.services.memory_consolidation import usage_limited

    return await usage_limited(db, context)


async def _audit_loop_limit(
    db: AsyncSession, run: WorkflowRun, node: DecisionNode, rounds: int, reason: str
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=run.user_id,
        owner_user_id=run.user_id,
        action=LOOP_LIMIT,
        target_type="workflow_run",
        target_id=run.id,
        target_owner_user_id=run.user_id,
        outcome="failure",
        reason_code=reason,
        metadata={
            "workflow_id": str(run.workflow_id),
            "decision_node_id": node.id,
            "rounds": rounds,
            "reason": reason,
        },
    )


async def _simple_node(
    db: AsyncSession,
    run: WorkflowRun,
    node: Node,
    position: int,
    values: dict[str, Any],
    round_no: int = 1,
) -> None:
    done: dict[str, Any] = {"status": "completed", "finished_at": _now(), "round": round_no}
    if isinstance(node, StartNode):
        await _step_row(db, run, node, position, output=dict(run.input), **done)
    elif isinstance(node, TransformNode):
        output = {
            "text": render(node.template, values),
            "fields": {name: field_value(t, values) for name, t in node.fields.items()},
        }
        await _step_row(db, run, node, position, output=output, **done)
    elif isinstance(node, OutputNode):
        output = {name: field_value(t, values) for name, t in node.fields.items()}
        await _step_row(db, run, node, position, output=output, **done)
        run.output = output
        _finish(run, "completed", None, None)
    elif isinstance(node, FailNode):
        await _step_row(db, run, node, position, output={"reason": node.reason}, **done)
        _finish(run, "failed", "workflow_fail_node", node.reason or "실패 종료 노드")
    elif isinstance(node, ApprovalNode):
        # The run waits for the runner's decision in the approval inbox (W2b).
        step = await _step_row(db, run, node, position, status="waiting", round=round_no)
        await workflow_approvals.request(db, run, step, node, values)
        run.status = "waiting"


def _record(step: WorkflowStepRun, outcome: agent_step.StepOutcome) -> None:
    step.status = outcome.status
    step.output = outcome.output
    step.error_code = outcome.error_code
    step.error_message = outcome.error_message
    step.finished_at = _now()


def _end_from_step(run: WorkflowRun, step: WorkflowStepRun) -> None:
    if step.status == "unknown":
        run.status = "unknown"
        run.error_code, run.error_message = step.error_code, step.error_message
        run.current_node = step.node_id
    elif step.status == "canceled":
        _finish(run, "canceled", "canceled", "실행을 취소했습니다")
    else:
        _finish(run, "failed", step.error_code or "step_failed", step.error_message)


def _context(job: JobContext, run: WorkflowRun) -> OrgContext:
    return OrgContext(org_id=job.org_id, tenant_id=job.tenant_id, user_id=run.user_id)


async def _locked(db: AsyncSession, run_id: uuid.UUID) -> WorkflowRun | None:
    return await db.scalar(select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update())


async def run_job(job: JobContext) -> dict[str, Any]:
    run_id = uuid.UUID(str(job.payload["run_id"]))
    while True:
        await job.progress(0)
        async with job.session() as db:
            run = await _locked(db, run_id)
            if run is None:
                return {"outcome": "gone"}
            if run.status in RUN_TERMINAL or run.status in ("unknown", "waiting"):
                return {"outcome": run.status}
            if run.status == "queued":
                run.status, run.started_at = "running", _now()
            context = _context(job, run)
            step, node = await _advance(db, run, context)
            prepared: agent_step.PreparedStep | agent_step.StepOutcome | None = None
            if step is not None and isinstance(node, AgentNode):
                # Checked again at every agent step (3.6): archived, or no longer runnable
                # by the runner since the run started (or while it waited for a decision).
                issues = await agent_node_issues(db, context, [node])
                if issues:
                    prepared = agent_step.StepOutcome(
                        "failed", error_code=issues[0].code, error_message=issues[0].message
                    )
                    _record(step, prepared)
                    _end_from_step(run, step)
            if step is not None and isinstance(node, AgentNode) and prepared is None:
                workflow = await db.get(Workflow, run.workflow_id)
                title = f"{workflow.name if workflow else '워크플로'} · {node.label or node.id}"
                prepared = await agent_step.prepare(
                    db,
                    context,
                    step,
                    node,
                    prompt=str((step.input or {}).get("prompt", "")),
                    title=title,
                )
                if isinstance(prepared, agent_step.StepOutcome):
                    _record(step, prepared)
                    _end_from_step(run, step)
                else:
                    step.status = "running"
            finished = run.status in RUN_TERMINAL
            if finished:
                await _audit_finish(db, run)
            await db.commit()
            if finished or run.status in ("waiting", "unknown"):
                return {"outcome": run.status}
        if isinstance(prepared, agent_step.PreparedStep) and step is not None:
            outcome = await agent_step.execute(context, step, prepared)
            async with job.session() as db:
                stored = await db.get(WorkflowStepRun, step.id, with_for_update=True)
                if outcome.status == "waiting":
                    # Paused before a tool that needs the runner: the inbox decision resumes it.
                    waiting_run = await _locked(db, run_id)
                    if stored is not None and stored.status not in ("completed", "failed"):
                        stored.status = "waiting"
                    if waiting_run is not None and waiting_run.status == "running":
                        waiting_run.status = "waiting"
                    await db.commit()
                    return {"outcome": "waiting"}
                if stored is not None:
                    _record(stored, outcome)
                await db.commit()


def register_jobs() -> None:
    if registry.get(RUN_JOB) is None:
        registry.register(registry.JobType(RUN_JOB, run_job, lease_seconds=1800, max_attempts=3))


__all__ = [
    "WorkflowRun",
    "RUN_CANCELED",
    "RUN_FINISHED",
    "RUN_JOB",
    "RUN_STARTED",
    "cancel_run",
    "fail_unknown",
    "get_run",
    "list_runs",
    "list_steps",
    "version_numbers",
    "register_jobs",
    "run_job",
    "run_not_found",
    "start_run",
]
