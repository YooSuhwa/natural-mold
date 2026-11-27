"""Authorize a graph-proved delegation recorded before child task enrollment existed."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import make_agent_runtime_name
from app.agent_runtime.offload_storage_types import GENERAL_PURPOSE_ACTOR
from app.authz.child_task_authorization import (
    _require_child_available,
    _require_delegation,
    _target,
)
from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.child_tasks.errors import ChildTaskStopped
from app.exceptions import AppError, NotFoundError
from app.models.agent import Agent
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_general_purpose_authority import require_general_purpose_authority
from app.services.ag_ui_pending_checkpoint import _run_lineage

if TYPE_CHECKING:
    from app.services.ag_ui_legacy_child_binding import LegacyChildBinding


def _denied() -> NotFoundError:
    return NotFoundError("CHILD_TASK_NOT_FOUND", "위임한 작업을 찾을 수 없습니다.")


async def _admissions(
    db: AsyncSession,
    *,
    conversation: Conversation,
    context: OrgContext,
    parent_id: UUID,
    producer_id: UUID | None,
    include_origin: bool = False,
) -> set[UUID]:
    if conversation.agent_id is None:
        raise _denied()
    lineage = await _run_lineage(db, parent_id, conversation.id, conversation.agent_id, context)
    if producer_id is None:
        if lineage != {parent_id}:
            raise _denied()
        return {parent_id}
    if producer_id not in lineage:
        raise _denied()
    if include_origin:
        return lineage
    if producer_id == parent_id:
        return {parent_id}
    edges = dict(
        (
            await db.execute(
                select(ConversationRun.id, ConversationRun.parent_run_id).where(
                    ConversationRun.id.in_(lineage)
                )
            )
        )
        .tuples()
        .all()
    )
    path = {parent_id}
    cursor = parent_id
    while cursor != producer_id:
        ancestor = edges.get(cursor)
        if ancestor is None or ancestor in path or ancestor not in lineage:
            raise _denied()
        path.add(ancestor)
        cursor = ancestor
    return path


async def _admitted_child(
    db: AsyncSession, *, runs: set[UUID], parent_id: UUID, runtime_name: str
) -> UUID:
    # Stored agent runtime names are immutable and derived from the UUID. Check
    # both to reject a nickname, a replaced role, or a descendant admitted only
    # through a different agent. The native tree snapshot records the full path.
    pin = ConversationRunAgent
    result = (
        await db.execute(
            select(pin.run_id, pin.agent_id_snapshot, pin.path)
            .join(Agent, Agent.id == pin.agent_id)
            .where(
                pin.run_id.in_(runs),
                pin.agent_id == pin.agent_id_snapshot,
                pin.role == "subagent",
                Agent.runtime_name == runtime_name,
            )
        )
    ).all()
    if {row.run_id for row in result} != runs:
        raise _denied()
    ids = {row.agent_id_snapshot for row in result}
    if len(ids) != 1:
        raise _denied()
    child_id = ids.pop()
    if make_agent_runtime_name(child_id) != runtime_name or any(
        row.path != [str(parent_id), str(child_id)] for row in result
    ):
        raise _denied()
    return child_id


async def require_legacy_child_binding(
    db: AsyncSession,
    *,
    conversation: Conversation,
    context: OrgContext,
    parent_run: ConversationRun,
    binding: LegacyChildBinding,
) -> UUID:
    """Require the original direct child, the same accepted accounts, and a fresh live grant."""
    root_id = conversation.agent_id
    if root_id is None or not binding.tool_call_id or not binding.checkpoint_ns:
        raise _denied()
    with db.no_autoflush:
        try:
            await require_private_actor(db, context)
            implicit_child = binding.runtime_name == GENERAL_PURPOSE_ACTOR
            runs = await _admissions(
                db,
                conversation=conversation,
                context=context,
                parent_id=parent_run.id,
                producer_id=binding.producer_run_id,
                include_origin=implicit_child,
            )
            if implicit_child:
                root = await require_general_purpose_authority(
                    db, context=context, conversation_id=conversation.id, runs=runs
                )
                await require_private_actor(db, context)
                if root != root_id or runs != await _admissions(
                    db,
                    conversation=conversation,
                    context=context,
                    parent_id=parent_run.id,
                    producer_id=binding.producer_run_id,
                    include_origin=True,
                ):
                    raise _denied()
                return root
            child = await _admitted_child(
                db, runs=runs, parent_id=root_id, runtime_name=binding.runtime_name
            )
            target = await _target(db, context, parent_run.id, child, legacy_attribution=True)
            if target.parent_id != root_id or target.conversation_id != conversation.id:
                raise _denied()
            await _require_delegation(target, context, child)
            if target.parent_owner == context.user_id and target.child_owner != context.user_id:
                # The owner's own chat used the same account in fixed and per_user
                # mode. The old row cannot distinguish the two FGA principals
                # that may delegate to somebody else's agent. Require both grants.
                other_mode = "fixed" if target.parent_identity == "per_user" else "per_user"
                await _require_delegation(
                    replace(target, parent_identity=other_mode), context, child
                )
            # Read-only snapshots may not hold the command's conversation lock.
            # Recheck the stored ancestry and pin after the external grant decision.
            await require_private_actor(db, context)
            if runs != await _admissions(
                db,
                conversation=conversation,
                context=context,
                parent_id=parent_run.id,
                producer_id=binding.producer_run_id,
            ) or child != await _admitted_child(
                db, runs=runs, parent_id=root_id, runtime_name=binding.runtime_name
            ):
                raise _denied()
            for run_id in runs:
                if target != await _target(db, context, run_id, child, legacy_attribution=True):
                    raise _denied()
            await _require_child_available(db, context, target, child)
        except ChildTaskStopped:
            raise _denied() from None
        except AppError as exc:
            if exc.status in {403, 404, 409}:
                raise _denied() from None
            raise
    return child
