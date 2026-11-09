"""``for_each`` and ``parallel`` steps: several agent executions in one step (nodes plan 3.1).

The step row is the parent; each agent execution is a child row (``parent_step_id``,
``branch_key`` = item index or branch name, ``node_id`` = ``<node>.<key>``) and is that
execution's envelope, exactly like an agent node's row: accepted once, never run again once
accepted, ``unknown`` when its result was not stored. Children run in batches (``parallel``
all at once, ``for_each`` a few at a time); a batch finishes before the next starts, so
between batches nothing is in flight. The parent completes when every child completed
(outputs in item or branch order) and ends with the first child that failed, was canceled
or is unknown. A tool that needs approval is refused inside a child (``block``): a parent
has no single point to resume from.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.workflow import WorkflowRun, WorkflowStepRun
from app.workflows import agent_step
from app.workflows.definition import (
    MAX_FOR_EACH,
    ForEachNode,
    ParallelNode,
    RefSegment,
    Template,
)
from app.workflows.values import agent_prompt, resolve

FOR_EACH_BATCH: Final = 3
_ENDED: Final = ("failed", "unknown", "canceled")

Fanout = ForEachNode | ParallelNode
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def referenced_files(template: Template, run: WorkflowRun) -> tuple[dict[str, str], ...]:
    """The ``file`` inputs a prompt references (N8), in order, once each."""
    files = run.files or {}
    names: dict[str, None] = {}
    for segment in template:
        if isinstance(segment, RefSegment) and segment.ref.node == "input":
            name = segment.ref.path.strip("/")
            if name in files:
                names[name] = None
    return tuple(dict(files[name]) for name in names)


def _children_spec(
    node: Fanout, values: dict[str, Any]
) -> list[tuple[str, str, str, Template]] | str:
    """``[(key, agent_id, prompt, template)]``, or an error code."""
    if isinstance(node, ParallelNode):
        return [
            (b.name, b.agent_id, agent_prompt(b.prompt, values), b.prompt) for b in node.branches
        ]
    items = resolve(values, node.source, node.path)
    if not isinstance(items, list):
        return "for_each_not_list"
    if len(items) > min(node.max_items, MAX_FOR_EACH):
        return "for_each_too_many"
    return [
        (
            str(index),
            node.agent_id,
            agent_prompt(node.prompt, {**values, "item": item}),
            node.prompt,
        )
        for index, item in enumerate(items)
    ]


async def start(
    db: AsyncSession,
    run: WorkflowRun,
    parent: WorkflowStepRun,
    node: Fanout,
    values: dict[str, Any],
) -> None:
    """Create the children (queued). A ``for_each`` over no items completes at once."""
    spec = _children_spec(node, values)
    if isinstance(spec, str):
        messages = {
            "for_each_not_list": "항목마다 노드의 대상 값이 목록이 아닙니다",
            "for_each_too_many": "항목이 최대 개수를 넘습니다",
        }
        parent.status, parent.error_code, parent.error_message = "failed", spec, messages[spec]
        parent.finished_at = _now()
        return
    for key, agent_id, prompt, template in spec:
        child = WorkflowStepRun(
            run_id=run.id,
            user_id=run.user_id,
            org_id=run.org_id,
            tenant_id=run.tenant_id,
            position=parent.position,
            node_id=f"{node.id}.{key}",
            node_type="agent",
            round=parent.round,
            status="queued",
            agent_id=uuid.UUID(agent_id),
            parent_step_id=parent.id,
            branch_key=key,
            input={
                "prompt": prompt,
                "files": list(referenced_files(template, run)),
            },
        )
        db.add(child)
    parent.input = {"count": len(spec)}
    parent.status = "running"
    if not spec:
        parent.status, parent.output, parent.finished_at = (
            "completed",
            {"items": [], "count": 0},
            _now(),
        )
    await db.flush()


async def children(db: AsyncSession, parent_id: uuid.UUID) -> list[WorkflowStepRun]:
    rows = await db.execute(
        select(WorkflowStepRun).where(WorkflowStepRun.parent_step_id == parent_id).with_for_update()
    )
    return list(rows.scalars())


def _ordered(node: Fanout, rows: list[WorkflowStepRun]) -> list[WorkflowStepRun]:
    if isinstance(node, ParallelNode):
        order = {branch.name: index for index, branch in enumerate(node.branches)}
        return sorted(rows, key=lambda row: order.get(row.branch_key or "", 0))
    return sorted(rows, key=lambda row: int(row.branch_key or 0))


def _output_schema(node: Fanout, key: str) -> dict[str, Any]:
    if isinstance(node, ForEachNode):
        return node.output_schema
    return next(branch.output_schema for branch in node.branches if branch.name == key)


def _finish_parent(node: Fanout, parent: WorkflowStepRun, rows: list[WorkflowStepRun]) -> bool:
    """Settle the parent once nothing is left to run; ``True`` when it ended."""
    ended = next((row for row in rows if row.status in _ENDED), None)
    if ended is not None:
        parent.status = ended.status
        parent.error_code = ended.error_code
        parent.error_message = ended.error_message
        parent.finished_at = _now()
        return True
    if all(row.status == "completed" for row in rows):
        outputs = [row.output for row in rows]
        if isinstance(node, ForEachNode):
            parent.output = {"items": outputs, "count": len(outputs)}
        else:
            parent.output = {str(row.branch_key): row.output for row in rows}
        parent.status, parent.finished_at = "completed", _now()
        return True
    return False


async def run_children(
    session: SessionFactory,
    context: OrgContext,
    run_id: uuid.UUID,
    parent_id: uuid.UUID,
    node: Fanout,
    title: str,
) -> None:
    """Run batches until the parent ends (the caller reads the parent's status after)."""
    from app.workflows.service import agent_node_issues

    batch_size = len(node.branches) if isinstance(node, ParallelNode) else FOR_EACH_BATCH
    while True:
        prepared: list[tuple[WorkflowStepRun, agent_step.PreparedStep]] = []
        async with session() as db:
            run = await db.scalar(
                select(WorkflowRun).where(WorkflowRun.id == run_id).with_for_update()
            )
            parent = await db.get(WorkflowStepRun, parent_id, with_for_update=True)
            if run is None or parent is None or parent.status not in ("queued", "running"):
                return
            rows = _ordered(node, await children(db, parent_id))
            for row in rows:
                if row.status in ("queued", "running") and row.conversation_run_id is not None:
                    outcome = await agent_step.recover(db, row)
                    if outcome is not None:
                        agent_step.record(row, outcome)
            if run.cancel_requested:
                for row in rows:
                    if row.status in ("queued", "running"):
                        agent_step.record(
                            row, agent_step.StepOutcome("canceled", error_code="canceled")
                        )
            if _finish_parent(node, parent, rows):
                await db.commit()
                return
            issues = await agent_node_issues(db, context, [node])
            if issues:
                for row in rows:
                    if row.status in ("queued", "running"):
                        agent_step.record(
                            row,
                            agent_step.StepOutcome(
                                "failed", error_code=issues[0].code, error_message=issues[0].message
                            ),
                        )
                _finish_parent(node, parent, rows)
                await db.commit()
                return
            pending = [row for row in rows if row.status in ("queued", "running")][:batch_size]
            for row in pending:
                step_input = row.input or {}
                result = await agent_step.prepare(
                    db,
                    context,
                    row,
                    agent_step.StepAgent(
                        agent_id=str(row.agent_id),
                        output_schema=_output_schema(node, row.branch_key or ""),
                        unattended="block",
                        files=tuple(step_input.get("files") or ()),
                    ),
                    prompt=str(step_input.get("prompt", "")),
                    title=f"{title} · {row.branch_key}",
                )
                if isinstance(result, agent_step.StepOutcome):
                    agent_step.record(row, result)
                else:
                    row.status = "running"
                    row.started_at = _now()
                    prepared.append((row, result))
            await db.commit()
        outcomes = await asyncio.gather(
            *(agent_step.execute(context, row, item) for row, item in prepared)
        )
        async with session() as db:
            for (row, _), outcome in zip(prepared, outcomes, strict=True):
                stored = await db.get(WorkflowStepRun, row.id, with_for_update=True)
                if stored is None:
                    continue
                if outcome.status == "waiting":  # ``block`` never pauses; never wait silently
                    outcome = agent_step.StepOutcome(
                        "failed", error_code="approval_blocked", error_message="승인이 필요한 도구"
                    )
                agent_step.record(stored, outcome)
            await db.commit()


__all__ = ["FOR_EACH_BATCH", "Fanout", "children", "referenced_files", "run_children", "start"]
