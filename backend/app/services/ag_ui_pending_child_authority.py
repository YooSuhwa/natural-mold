"""Authorize the children a standard chat resume will advance, including after a disconnect."""

from collections.abc import Sequence
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.lease_checkpoint_namespace import NS_SEP
from app.authz.child_task_authorization import (
    _require_child_available,
    _require_delegation,
    _target,
)
from app.authz.execution_lock_cursor import ExecutionProofError
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import acquire_execution_proof
from app.authz.private_actor import require_private_actor
from app.child_tasks.errors import ChildTaskStopped
from app.exceptions import AppError, NotFoundError
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.models.organization import Organization
from app.services.ag_ui_pending_checkpoint import PendingCheckpoint, _run_lineage


def _denied() -> NotFoundError:
    return NotFoundError("CHILD_TASK_NOT_FOUND", "위임한 작업을 찾을 수 없습니다.")


class _Child(NamedTuple):
    id: UUID
    origin: UUID
    current: UUID
    agent: UUID
    tool_call_id: str
    namespace: str
    status: str


async def _child_rows(db: AsyncSession, lineage: set[UUID]) -> list[_Child]:
    child = ConversationChildTask
    rows = await db.execute(
        select(
            child.id,
            child.run_id,
            child.current_run_id,
            child.child_agent_id,
            child.tool_call_id,
            child.checkpoint_ns,
            child.status,
        )
        .where(child.current_run_id.in_(lineage))
        .order_by(child.id)
    )
    return [_Child(*row) for row in rows]


def _bound_slots(rows: Sequence[_Child], pending: PendingCheckpoint) -> dict[str, tuple[str, ...]]:
    bound: dict[str, tuple[str, ...]] = {}
    for slot in pending.interrupts:
        namespace = tuple(slot["ns"])
        root_task_id = pending.root_task_ids.get(slot["id"])
        matches: list[_Child] = []
        task_matches = False
        for row in rows:
            prefix = tuple(row.namespace.split(NS_SEP))
            # A cancelled delegated task can still own an unanswered root interrupt.
            # The root's durable Pregel task ID survives a missing journal namespace.
            matching_task = bool(root_task_id and prefix[0].partition(":")[2] == root_task_id)
            matching_ns = bool(namespace and row.namespace and namespace[: len(prefix)] == prefix)
            task_matches = task_matches or matching_task
            if matching_ns and root_task_id and not matching_task:
                raise _denied()
            if matching_ns or (not namespace and matching_task):
                matches.append(row)
        if namespace and task_matches and not matches:
            raise _denied()
        if len({row.namespace for row in matches}) != len(matches):
            raise _denied()
        if any(row.status != "awaiting_approval" for row in matches):
            raise _denied()
        if matches:
            native = pending.legacy_child_bindings.get(slot["id"])
            if native is not None:
                matches = [
                    row
                    for row in matches
                    if row.tool_call_id == native.tool_call_id
                    and row.namespace == native.checkpoint_ns
                ]
                if len(matches) != 1:
                    raise _denied()
            if not namespace and len(matches) != 1:
                raise _denied()
            bound[slot["id"]] = namespace or tuple(matches[0].namespace.split(NS_SEP))
    return bound


async def _check_tracked(
    db: AsyncSession,
    context: OrgContext,
    parent: ConversationRun,
    conversation: Conversation,
    child: _Child,
) -> None:
    current = await _target(db, context, child.current, child.agent)
    if current.conversation_id != conversation.id or current.parent_id != conversation.agent_id:
        raise _denied()
    await _require_delegation(current, context, child.agent)
    for run_id in {child.origin, child.current, parent.id}:
        if current != await _target(db, context, run_id, child.agent):
            raise _denied()
    await _require_child_available(db, context, current, child.agent)


async def _check_batch(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    parent: ConversationRun,
    pending: PendingCheckpoint,
    *,
    lock: bool,
) -> frozenset[str]:
    if conversation.source == INDEPENDENT_JOB_SOURCE:
        return frozenset()  # The independent child has its own conversation and F05 authority.
    if (
        parent.conversation_id != conversation.id
        or parent.agent_id is None
        or parent.agent_id != conversation.agent_id
        or parent.user_id != context.user_id
        or (conversation.user_id, conversation.org_id, conversation.tenant_id)
        != (context.user_id, context.org_id, context.tenant_id)
        or (parent.org_id, parent.tenant_id)
        not in {(None, None), (context.org_id, context.tenant_id)}
        or parent.status != "interrupted"
    ):
        raise _denied()
    lineage = await _run_lineage(db, parent.id, conversation.id, parent.agent_id, context)
    if parent.id not in lineage:
        raise _denied()
    rows = await _child_rows(db, lineage)
    bound = _bound_slots(rows, pending)
    named = {slot["id"] for slot in pending.interrupts if slot["ns"]}
    legacy = {
        ident: binding
        for ident, binding in pending.legacy_child_bindings.items()
        if ident not in bound
    }
    if named - bound.keys() - legacy.keys():
        enrolled = bool(rows) or await db.scalar(
            select(ConversationRunAgent.id)
            .where(
                ConversationRunAgent.run_id == parent.id,
                ConversationRunAgent.identity_mode_at_accept.is_not(None),
            )
            .limit(1)
        )
        if enrolled:
            raise _denied()
    # Standard resume covers every open interrupt. A retained F04 row remains
    # authoritative when an old journal omitted the namespace on its interrupt.
    waiting = [row for row in rows if row.status == "awaiting_approval"]
    if not waiting and not legacy:
        return frozenset()
    proof = None
    if lock:
        proof = await acquire_execution_proof(
            db, context, root_agent_id=parent.agent_id, conversation_ids=(conversation.id,)
        )
    await require_private_actor(db, context)
    epoch = select(Organization.authz_epoch).where(Organization.id == context.org_id)
    before = await db.scalar(epoch)
    if before is None:
        raise _denied()
    checked: set[tuple[UUID, UUID, UUID]] = set()
    for child in waiting:
        if child.origin not in lineage or (proof and child.agent not in proof.agent_ids):
            raise _denied()
        key = (child.origin, child.current, child.agent)
        if key not in checked:
            await _check_tracked(db, context, parent, conversation, child)
            checked.add(key)
    for binding in legacy.values():
        if binding is None:
            raise _denied()
        from app.services.ag_ui_legacy_child_authority import require_legacy_child_binding

        agent_id = await require_legacy_child_binding(
            db, conversation=conversation, context=context, parent_run=parent, binding=binding
        )
        if proof and agent_id not in proof.agent_ids:
            raise _denied()
    if rows != await _child_rows(db, lineage) or before != await db.scalar(epoch):
        raise _denied()
    await require_private_actor(db, context)
    # Only successful, whole-batch proof can attribute a missing namespace. The
    # current snapshot, the native response and its claim all use this same path.
    namespaces = dict(bound)
    for ident, binding in legacy.items():
        if binding is not None:
            namespaces[ident] = tuple(binding.checkpoint_ns.split(NS_SEP))
    for slot in pending.interrupts:
        if not slot["ns"] and slot["id"] in namespaces:
            slot["ns"] = list(namespaces[slot["id"]])
    return frozenset(legacy)


async def _authorize(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    parent: ConversationRun,
    pending: PendingCheckpoint,
    *,
    lock: bool,
) -> frozenset[str]:
    try:
        with db.no_autoflush:
            return await _check_batch(db, conversation, context, parent, pending, lock=lock)
    except (ChildTaskStopped, ExecutionProofError):
        raise _denied() from None
    except AppError as exc:
        if exc.status in {403, 404, 409}:
            raise _denied() from None
        raise


async def read_pending_child_authority(
    db: AsyncSession,
    *,
    conversation: Conversation,
    context: OrgContext,
    parent_run: ConversationRun,
    pending: PendingCheckpoint,
) -> frozenset[str]:
    """Prove the whole batch without locks or writes. Return actionable pre-F04 child slot IDs."""
    return await _authorize(db, conversation, context, parent_run, pending, lock=False)


async def require_pending_child_authority(
    db: AsyncSession,
    *,
    conversation: Conversation,
    context: OrgContext,
    parent_run: ConversationRun,
    pending: PendingCheckpoint,
) -> frozenset[str]:
    """Require current delegation within the submitting or delivering transaction."""
    return await _authorize(db, conversation, context, parent_run, pending, lock=True)
