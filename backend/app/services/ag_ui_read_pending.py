"""Verify outstanding approvals against their active, authorized checkpoint."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.agent_runtime.lease_checkpoint_namespace import NS_SEP
from app.agent_runtime.protocol_events import ProtocolInterrupt
from app.approvals import service as approvals
from app.authz.org_context import OrgContext
from app.exceptions import NotFoundError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services import conversation_run_service
from app.services.ag_ui_child_terminal_statuses import proven_child_terminal_statuses
from app.services.ag_ui_historical_child_visibility import historical_child_visibility
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
    child_authority_denied: bool = False
    legacy_child_ids: frozenset[str] = frozenset()
    legacy_bindings: dict[str, LegacyChildBinding] = field(default_factory=dict)
    child_terminal_statuses: dict[tuple[str, ...], str] = field(default_factory=dict)

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
    return await _read_run_proof(db, conversation, context, current, checkpoint_id=checkpoint_id)


async def constrain_legacy_child_visibility(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    run: ConversationRun,
    visibility: ChildVisibility,
) -> ChildVisibility:
    """Fence journal replay before its first child frame, also for superseded interrupts."""
    if not visibility.allow_legacy_details or run.status != "interrupted":
        return visibility
    historical = await historical_child_visibility(db, conversation, context, run)
    if historical is not None:
        return historical
    # The default legacy journal policy predates recipient-level grants. A
    # superseding turn cannot authorize frames belonging to this old physical run.
    proof = await _read_run_proof(db, conversation, context, run)
    if not proof.interrupts or proof.child_authority_denied:
        return ChildVisibility(False, frozenset(), frozenset())
    return visibility


async def _read_run_proof(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    run: ConversationRun,
    *,
    checkpoint_id: str | None = None,
) -> CurrentReadInterrupts:
    checkpoint = await read_pending_checkpoint(
        db, conversation=conversation, context=context, parent_run=run
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
    denied = False
    try:
        legacy_ids = await read_pending_child_authority(
            db, conversation=conversation, context=context, parent_run=run, pending=checkpoint
        )
    except NotFoundError:
        legacy_ids = frozenset()
        denied = True
        actionable = False

    slots: list[ProtocolInterrupt] = []
    bindings: dict[str, LegacyChildBinding] = {}
    for slot in checkpoint.interrupts:
        ident = slot["id"]
        ns = tuple(slot["ns"])
        if ident in legacy_ids:
            binding = checkpoint.legacy_child_bindings.get(ident)
            if binding is None:
                denied = True
                actionable = False
                continue
            native = tuple(binding.checkpoint_ns.split(NS_SEP))
            if not all(native) or (ns and ns[: len(native)] != native):
                denied = True
                actionable = False
                continue
            ns = ns or native
            bindings[ident] = binding
        elif ident in checkpoint.legacy_child_bindings and not ns:
            # An unanswered root Pregel task can represent an entire child graph.
            # Its interrupt never becomes a root card solely because the journal expired.
            denied = True
            actionable = False
            continue
        kind = checkpoint.native_kinds.get(ident)
        value = slot["value"]
        if kind in ("ask_user", "auth_required"):
            # Only a typed, unanswered native interrupt can supply either form.
            # An approval that happens to invoke ask_user keeps its review policy.
            value = checkpoint.native_prompt_values.get(ident, {"type": "unknown"})
            if value.get("type") != kind:
                actionable = False
        elif kind != "approval":
            actionable = False
        slots.append(ProtocolInterrupt(id=ident, value=value, ns=list(ns)))
    if denied:
        # A batch denial is deliberately anonymous. Preserve genuine root
        # declarations, and disclose no child's prompt, expiry, or grant marker.
        slots = [
            s for s in slots if not s["ns"] and s["id"] not in checkpoint.legacy_child_bindings
        ]
        bindings.clear()
    return CurrentReadInterrupts(
        checkpoint_id=checkpoint.checkpoint_id,
        interrupts=tuple(slots),
        expires_at={
            slot["id"]: deadlines[slot["id"]].isoformat()
            for slot in slots
            if slot["id"] in deadlines
        },
        actionable=actionable,
        child_authority_denied=denied,
        legacy_child_ids=frozenset(bindings) if actionable else frozenset(),
        legacy_bindings=bindings,
    )


async def current_read_interrupts(
    db: AsyncSession, conversation: Conversation, context: OrgContext, target: ReadTarget
) -> CurrentReadInterrupts:
    if target.run is None:
        return CurrentReadInterrupts()
    pending = await read_proven_interrupts(db, conversation, context, target.run)
    statuses = await proven_child_terminal_statuses(
        db, conversation=conversation, context=context, run=target.run, visibility=target.visibility
    )
    return replace(pending, child_terminal_statuses=statuses) if statuses else pending
