"""Recheck every suspended linked child before accepting or delivering its chat resume."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.lease_checkpoint_namespace import NS_SEP
from app.approvals.emergency import refuse_run
from app.authz.child_task_authorization import _require_delegation, _target
from app.authz.execution_lock_cursor import ExecutionProofError
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import acquire_execution_proof
from app.child_tasks.errors import ChildTaskStopped
from app.child_tasks.persistence import _resume_lineage
from app.exceptions import AppError, NotFoundError
from app.guardrails.policy import resolve_tool_risk
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_pending_checkpoint import PendingCheckpoint
from app.tools.keys import agent_ref


def _denied() -> NotFoundError:
    return NotFoundError("CHILD_TASK_NOT_FOUND", "위임한 작업을 찾을 수 없습니다.")


def _required_children(
    rows: Sequence[ConversationChildTask], pending: PendingCheckpoint, *, enrolled: bool
) -> list[ConversationChildTask]:
    # LangGraph's durable root Interrupt has no namespace. Resuming a standard batch
    # answers every open slot, so a lost journal must not drop a waiting participant.
    required = [row for row in rows if row.status == "awaiting_approval"]
    for slot in pending.interrupts:
        namespace = tuple(slot["ns"])
        root_task_id = pending.root_task_ids.get(slot["id"])
        matches = []
        for row in rows:
            prefix = tuple(row.checkpoint_ns.split(NS_SEP))
            # A delegated tool is invoked in the root Pregel task's namespace.
            # Even after cancellation its suspended interrupt can remain in the
            # saver, with the root task id intact but without a journal namespace.
            matches_root_task = (
                not namespace and root_task_id and prefix[0].partition(":")[2] == root_task_id
            )
            if matches_root_task or (row.checkpoint_ns and namespace[: len(prefix)] == prefix):
                matches.append(row)
        if not matches:
            if namespace and enrolled:
                raise _denied()
            continue
        if len({r.checkpoint_ns for r in matches}) != len(matches):
            raise _denied()
        if any(row.status != "awaiting_approval" for row in matches):
            raise _denied()
    return required


async def require_pending_child_authority(
    db: AsyncSession,
    *,
    conversation: Conversation,
    context: OrgContext,
    parent_run: ConversationRun,
    pending: PendingCheckpoint,
) -> None:
    """Authorize checkpointed children as their original participants, including abandonment."""
    if conversation.source == INDEPENDENT_JOB_SOURCE:
        # These children have a separate conversation, lineage, and F05 execution gate.
        return
    if (
        parent_run.conversation_id != conversation.id
        or parent_run.user_id != context.user_id
        or (conversation.user_id, conversation.org_id, conversation.tenant_id)
        != (context.user_id, context.org_id, context.tenant_id)
        or parent_run.status != "interrupted"
    ):
        raise _denied()
    if parent_run.tenant_id is None and parent_run.org_id is None:
        # Child enrollment was introduced after chat run attribution. A tracked row
        # on an unattributed run cannot inherit legacy behavior.
        existing = await db.scalar(
            select(ConversationChildTask.id)
            .where(ConversationChildTask.current_run_id == parent_run.id)
            .limit(1)
        )
        if existing is not None:
            raise _denied()
        return
    if (parent_run.org_id, parent_run.tenant_id) != (context.org_id, context.tenant_id):
        raise _denied()
    lineage = _resume_lineage(parent_run)
    query = select(ConversationChildTask).where(ConversationChildTask.current_run_id.in_(lineage))
    # F04 registers the child before it can stop the graph. Once the root is
    # interrupted, every legitimate linked invocation already has its row.
    has_tasks = await db.scalar(query.with_only_columns(ConversationChildTask.id).limit(1))
    named = any(slot["ns"] for slot in pending.interrupts)
    if not has_tasks and not named:
        return
    try:
        proof = await acquire_execution_proof(
            db, context, root_agent_id=conversation.agent_id, conversation_ids=(conversation.id,)
        )
        rows = (
            await db.scalars(
                query.order_by(ConversationChildTask.id).execution_options(populate_existing=True)
            )
        ).all()
        enrolled = bool(rows)
        if named and not enrolled:
            enrolled = bool(
                await db.scalar(
                    select(ConversationRunAgent.id)
                    .where(
                        ConversationRunAgent.run_id == parent_run.id,
                        ConversationRunAgent.identity_mode_at_accept.is_not(None),
                    )
                    .limit(1)
                )
            )
        children = _required_children(rows, pending, enrolled=enrolled)
        if not children:
            return
        ancestors = set(await db.scalars(lineage))
        checked: set[tuple[UUID, UUID, UUID]] = set()
        for child in children:
            if child.run_id not in ancestors:
                raise _denied()
            key = (child.current_run_id, child.run_id, child.child_agent_id)
            if key in checked:
                continue
            checked.add(key)
            target_id = child.child_agent_id
            if target_id not in proof.agent_ids or conversation.agent_id not in proof.agent_ids:
                raise _denied()
            current = await _target(db, context, child.current_run_id, target_id)
            if (
                current.conversation_id != conversation.id
                or current.parent_id != conversation.agent_id
            ):
                raise _denied()
            await _require_delegation(current, context, target_id)
            for run_id in {child.run_id, child.current_run_id, parent_run.id}:
                if current != await _target(db, context, run_id, target_id):
                    raise _denied()
            policy = await resolve_tool_risk(db, context.tenant_id, current.profile)
            if policy and policy.switched_off(agent_ref(target_id, current.child_name)):
                raise _denied()
            await refuse_run(db, tenant_id=context.tenant_id, agent_id=current.parent_id)
            await refuse_run(db, tenant_id=context.tenant_id, agent_id=target_id)
    except (ChildTaskStopped, ExecutionProofError):
        raise _denied() from None
    except AppError as exc:
        if exc.status in {403, 404, 409}:
            raise _denied() from None
        raise
