"""Read-only authorization for a pre-enrollment chat after its approval was consumed."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.agent_runtime.conversation_checkpointer import loaded_conversation_checkpointer
from app.agent_runtime.lease_checkpoint_namespace import NS_SEP
from app.authz.conversation_run_scope import private_run_predicate
from app.authz.conversation_scope import require_private_conversation
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_legacy_child_authority import require_legacy_child_binding
from app.services.ag_ui_legacy_child_binding import read_historical_child_bindings
from app.services.ag_ui_snapshot_history import _select_lineage
from app.tenancy.checkpoint_audit import CHECKPOINT_TENANT_KEY

_HIDDEN = ChildVisibility(False, frozenset(), frozenset())


class _Successor(NamedTuple):
    run_id: UUID
    status: str
    original_checkpoint: str | None


class _Original(NamedTuple):
    saved: CheckpointTuple
    latest_root_id: str


async def _successors(
    db: AsyncSession, conversation: Conversation, context: OrgContext, parent: ConversationRun
) -> list[_Successor]:
    run, receipt = ConversationRun, AgUiCommandReceipt
    rows = await db.execute(
        select(run.id, run.status, receipt.checkpoint_id)
        .outerjoin(
            receipt,
            and_(
                receipt.run_id == run.id,
                receipt.input_id.is_(None),
                receipt.conversation_id == conversation.id,
                private_scope_predicate(receipt, context),
            ),
        )
        .where(
            run.parent_run_id == parent.id,
            run.conversation_id == conversation.id,
            run.agent_id == parent.agent_id,
            run.source == "resume",
            private_run_predicate(context),
            or_(
                and_(run.org_id == context.org_id, run.tenant_id == context.tenant_id),
                and_(run.org_id.is_(None), run.tenant_id.is_(None)),
            ),
        )
        .limit(2)
    )
    return [_Successor(*row) for row in rows]


def _root_id(config: Mapping[str, Any], thread: str) -> str | None:
    values = config.get("configurable")
    if (
        not isinstance(values, Mapping)
        or values.get("thread_id") != thread
        or values.get("checkpoint_ns") != ""
    ):
        return None
    ident = values.get("checkpoint_id")
    return ident if isinstance(ident, str) and ident else None


async def _original_checkpoint(
    saver: BaseCheckpointSaver[Any],
    conversation: Conversation,
    context: OrgContext,
    issuer: UUID,
    successor: _Successor,
) -> _Original | None:
    """Require the resumed issuer's checkpoint and an actual successor write on one chosen path."""
    thread = str(conversation.id)
    parents: dict[str, str | None] = {}
    resumed: set[str] = set()
    original = None
    async for row in saver.alist({"configurable": {"thread_id": thread, "checkpoint_ns": ""}}):
        cid = _root_id(row.config, thread)
        parent = _root_id(row.parent_config, thread) if row.parent_config else None
        if (
            not cid
            or cid in parents
            or cid != row.checkpoint.get("id")
            or (row.parent_config and not parent)
        ):
            return None
        tenant = row.metadata.get(CHECKPOINT_TENANT_KEY)
        if tenant not in (None, str(context.tenant_id)):
            return None
        parents[cid] = parent
        producer = row.metadata.get("moldy_run_id")
        if tenant == str(context.tenant_id) and producer == str(successor.run_id):
            resumed.add(cid)
        if (
            cid == successor.original_checkpoint
            and tenant == str(context.tenant_id)
            and producer == str(issuer)
        ):
            original = row
    path = _select_lineage(parents, conversation.active_branch_checkpoint_id)
    target = successor.original_checkpoint
    if (
        not original
        or target is None
        or target not in path
        or not resumed.intersection(path[path.index(target) + 1 :])
    ):
        return None
    return _Original(original, next(iter(parents)))


async def historical_child_visibility(
    db: AsyncSession, conversation: Conversation, context: OrgContext, run: ConversationRun
) -> ChildVisibility | None:
    """None denotes a turn with no successor. A superseded turn only gets proved read access."""
    with db.no_autoflush:
        conversation = await require_private_conversation(db, conversation.id, context)
        successors = await _successors(db, conversation, context, run)
        if not successors:
            return None
        if (
            len(successors) != 1
            or successors[0].status not in {"interrupted", "completed"}
            or not successors[0].original_checkpoint
            or conversation.agent_lifecycle_state == "purged"
            or conversation.source != "ui"
            or run.conversation_id != conversation.id
        ):
            return _HIDDEN
        selection = conversation.active_branch_checkpoint_id
        try:
            saver = await loaded_conversation_checkpointer(conversation, context, db=db)
            original = await _original_checkpoint(
                saver, conversation, context, run.id, successors[0]
            )
            if original is None:
                return _HIDDEN
            children = await read_historical_child_bindings(
                saver, original.saved, tenant_id=context.tenant_id, lineage={run.id}
            )
            if not children:
                return _HIDDEN
            pairs: set[tuple[str, tuple[str, ...]]] = set()
            for child in children:
                await require_legacy_child_binding(
                    db, conversation=conversation, context=context, parent_run=run, binding=child
                )
                pairs.add((child.tool_call_id, tuple(child.checkpoint_ns.split(NS_SEP))))
            current = await require_private_conversation(db, conversation.id, context)
            thread = str(current.id)
            head = await saver.aget_tuple(
                {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
            )
            if (
                head is None
                or _root_id(head.config, thread) != original.latest_root_id
                or current.active_branch_checkpoint_id != selection
                or current.agent_lifecycle_state == "purged"
                or current.agent_id != run.agent_id
                or await _successors(db, current, context, run) != successors
            ):
                return _HIDDEN
            return ChildVisibility(
                False,
                frozenset(call for call, _ in pairs),
                frozenset(ns for _, ns in pairs),
                historical_bindings=frozenset(pairs),
            )
        except (AppError, RuntimeError, TypeError, ValueError, AttributeError, KeyError):
            # An unavailable or ambiguous historic root cannot certify prior child output.
            return _HIDDEN
