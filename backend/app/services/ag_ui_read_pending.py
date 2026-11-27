"""Verify outstanding approvals against their active, authorized checkpoint."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.lease_checkpoint_namespace import NS_SEP
from app.agent_runtime.protocol_events import ProtocolInterrupt
from app.approvals import service as approvals
from app.authz.org_context import OrgContext
from app.exceptions import NotFoundError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_service
from app.services.ag_ui_pending_checkpoint import read_pending_checkpoint
from app.services.ag_ui_pending_child_authority import read_pending_child_authority

if TYPE_CHECKING:
    from app.services.ag_ui_legacy_child_binding import LegacyChildBinding
    from app.services.ag_ui_read_target import ReadTarget


@dataclass(frozen=True, slots=True)
class CurrentReadInterrupts:
    checkpoint_id: str | None = None
    interrupts: tuple[ProtocolInterrupt, ...] = ()
    expires_at: dict[str, str] = field(default_factory=dict)
    actionable: bool = False
    legacy_child_ids: frozenset[str] = frozenset()
    legacy_bindings: dict[str, LegacyChildBinding] = field(default_factory=dict)

    @property
    def legacy_namespaces(self) -> dict[str, tuple[str, ...]]:
        return {
            ident: tuple(binding.checkpoint_ns.split(NS_SEP))
            for ident, binding in self.legacy_bindings.items()
        }


async def read_proven_interrupts(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    run: ConversationRun,
    *,
    checkpoint_id: str | None = None,
) -> CurrentReadInterrupts:
    """Derive display and batch decision authority from the same checkpoint as resume."""
    if run.status != "interrupted":
        return CurrentReadInterrupts()
    readable = await conversation_run_service.readable_conversation_run_ids(
        db, conversation, context
    )
    current = await conversation_run_service.get_latest_interrupted_run(
        db, conversation_id=conversation.id, context=context, readable_run_ids=readable
    )
    if current is None or current.id != run.id:
        return CurrentReadInterrupts()
    checkpoint = await read_pending_checkpoint(
        db, conversation=conversation, context=context, parent_run=current
    )
    if checkpoint is None or (
        checkpoint_id is not None and checkpoint_id != checkpoint.checkpoint_id
    ):
        return CurrentReadInterrupts()
    deadlines = {key: approvals.aware(value) for key, value in checkpoint.expires_at.items()}
    now = approvals.now()
    actionable = bool(checkpoint.interrupts) and all(
        deadline > now for deadline in deadlines.values()
    )
    authorized = True
    try:
        legacy_ids = await read_pending_child_authority(
            db, conversation=conversation, context=context, parent_run=current, pending=checkpoint
        )
    except NotFoundError:
        legacy_ids = frozenset()
        authorized = False
        actionable = False

    slots: list[ProtocolInterrupt] = []
    bindings: dict[str, LegacyChildBinding] = {}
    for slot in checkpoint.interrupts:
        ident = slot["id"]
        ns = tuple(slot["ns"])
        if not authorized and (ns or ident in checkpoint.legacy_child_bindings):
            continue
        if ident in legacy_ids:
            binding = checkpoint.legacy_child_bindings.get(ident)
            if binding is None:
                actionable = False
                continue
            native = tuple(binding.checkpoint_ns.split(NS_SEP))
            if not all(native) or (ns and ns[: len(native)] != native):
                actionable = False
                continue
            ns = ns or native
            bindings[ident] = binding
        elif ident in checkpoint.legacy_child_bindings and not ns:
            # An unanswered root Pregel task can represent an entire child graph.
            # Its interrupt never becomes a root card solely because the journal expired.
            actionable = False
            continue
        if checkpoint.native_kinds.get(ident) not in {"approval", "ask_user", "auth_required"}:
            actionable = False
        slots.append(ProtocolInterrupt(id=ident, value=slot["value"], ns=list(ns)))
    return CurrentReadInterrupts(
        checkpoint_id=checkpoint.checkpoint_id,
        interrupts=tuple(slots),
        expires_at={
            slot["id"]: deadlines[slot["id"]].isoformat()
            for slot in slots
            if slot["id"] in deadlines
        },
        actionable=actionable,
        legacy_child_ids=frozenset(bindings) if actionable else frozenset(),
        legacy_bindings=bindings,
    )


async def current_read_interrupts(
    db: AsyncSession, conversation: Conversation, context: OrgContext, target: ReadTarget
) -> CurrentReadInterrupts:
    if target.run is None:
        return CurrentReadInterrupts()
    return await read_proven_interrupts(db, conversation, context, target.run)
