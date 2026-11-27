"""Admit AG-UI 1.0 resume entries against Moldy's current checkpoint and approval claims."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.protocol_redaction import REDACTED_SENSITIVE_FIELD
from app.approvals import service as approvals
from app.approvals.recording import decision_types
from app.authz.org_context import OrgContext
from app.exceptions import ConflictError, ValidationError
from app.models.agent_lifecycle import ConversationRunAgent
from app.models.approval_request import ApprovalRequest
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.routers.conversation_agent_protocol_interrupts import ThreadInterrupt
from app.routers.conversation_agent_protocol_resume import ResumePayload, SubmittedInterruptResponse
from app.routers.conversation_resume_decisions import INVALID, STALE
from app.services import conversation_run_service
from app.services.ag_ui_pending_checkpoint import PendingCheckpoint, read_pending_checkpoint
from app.services.ag_ui_pending_child_authority import require_pending_child_authority
from app.services.ag_ui_resume_decisions import checkpoint_response

if TYPE_CHECKING:
    from ag_ui.core import ResumeEntry


@dataclass(frozen=True, slots=True)
class VerifiedResume:
    parent_run: ConversationRun
    checkpoint_id: str
    resume: ResumePayload
    pending_interrupts: list[ThreadInterrupt]
    expired_abandonments: frozenset[str]
    approval_rows_by_run: dict[UUID, dict[str, UUID]]
    reconstructed_legacy_rows: frozenset[UUID]

    @property
    def approval_request_ids(self) -> dict[str, UUID]:
        return self.approval_rows_by_run.get(self.parent_run.id, {})

    @property
    def inherited_approval_request_ids(self) -> dict[str, list[UUID]]:
        inherited: dict[str, list[UUID]] = {}
        for run_id, rows in self.approval_rows_by_run.items():
            if run_id != self.parent_run.id:
                for ident, row_id in rows.items():
                    inherited.setdefault(ident, []).append(row_id)
        return inherited

    @property
    def input_payload(self) -> dict[str, Any]:
        return self.resume.input_payload


def _stale() -> ConflictError:
    return ConflictError(STALE, "The conversation no longer waits for these interrupt decisions")


async def _restorable_legacy_rows(
    db: AsyncSession,
    parent: ConversationRun,
    created: list[ApprovalRequest],
    checkpoint: PendingCheckpoint,
    verified_legacy_ids: frozenset[str],
) -> frozenset[UUID]:
    # A historical native delegation may still be waiting after losing both its
    # card and approval row. Keep replacement displays masked; only the original
    # checkpoint's choice, confirmed against the fresh grant, can be recorded.
    sources: dict[UUID, UUID] = {}
    for row in created:
        binding = checkpoint.legacy_child_bindings.get(row.interrupt_id)
        if (
            row.interrupt_id in verified_legacy_ids
            and binding is not None
            and row.actions
            and all(
                action.get("name") == action.get("args") == REDACTED_SENSITIVE_FIELD
                for action in row.actions
            )
        ):
            sources[row.id] = binding.producer_run_id or parent.id
    if not sources:
        return frozenset()
    # Today's SDK builtin also has no child-task row. Only a pre-enrollment
    # participant pin authorizes reconstructing a decision from the old transport.
    original_runs = set(
        await db.scalars(
            select(ConversationRunAgent.run_id).where(
                ConversationRunAgent.run_id.in_(set(sources.values())),
                ConversationRunAgent.role == "root",
                ConversationRunAgent.agent_id == parent.agent_id,
                ConversationRunAgent.agent_id_snapshot == parent.agent_id,
                ConversationRunAgent.identity_mode_at_accept.is_(None),
            )
        )
    )
    return frozenset(row_id for row_id, run_id in sources.items() if run_id in original_runs)


async def _lock_approval_rows(
    db: AsyncSession,
    parent: ConversationRun,
    conversation: Conversation,
    slots: Mapping[str, ThreadInterrupt],
    context: OrgContext,
    checkpoint: PendingCheckpoint,
    verified_legacy_ids: frozenset[str],
) -> tuple[list[ApprovalRequest], frozenset[UUID]]:
    """Serialize with the recorder that follows the run's committed interrupted transition."""
    tool_slots = [
        approvals.PendingInterrupt(ident, item["value"], tuple(item["ns"]))
        for ident, item in slots.items()
        if approvals.approval_actions(item["value"]) is not None
    ]
    reconstructed: frozenset[UUID] = frozenset()
    if tool_slots:
        # The recorder and resume can race after an interrupt is published. Its native
        # uniqueness/parent lock binds any missing current approval to this same run.
        # None keeps raw credentials out of displays when no redacted event was retained.
        created = await approvals.record_pending(
            db,
            parent,
            tool_slots,
            origin=approvals.RunOrigin(agent_name=conversation.agent_name_snapshot),
            secret_values=None,
        )
        reconstructed = await _restorable_legacy_rows(
            db, parent, created, checkpoint, verified_legacy_ids
        )
    keys = {(parent.id, ident) for ident in slots}
    keys.update(
        (run_id, ident)
        for ident, run_ids in checkpoint.approval_run_ids.items()
        for run_id in run_ids
    )
    rows = await db.scalars(
        select(ApprovalRequest)
        .where(tuple_(ApprovalRequest.run_id, ApprovalRequest.interrupt_id).in_(keys))
        .order_by(ApprovalRequest.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    result = list(rows)
    for row in result:
        value = slots[row.interrupt_id]["value"]
        if (
            row.status != "pending"
            or row.kind != "tool"
            or row.subject_user_id != context.user_id
            or row.decider_user_id != context.user_id
            or row.conversation_id != conversation.id
            or row.tenant_id != context.tenant_id
            or row.org_id != context.org_id
            or approvals.approval_actions(value) is None
            or row.args_digest != approvals.args_digest(value)
        ):
            raise _stale()
    found = {(row.run_id, row.interrupt_id) for row in result}
    if not all(
        (run_id, ident) in found
        for ident, run_ids in checkpoint.approval_run_ids.items()
        for run_id in run_ids
    ):
        raise _stale()
    return result, reconstructed


async def verify_ag_ui_resume(
    db: AsyncSession,
    *,
    conversation: Conversation,
    context: OrgContext,
    entries: Sequence[ResumeEntry],
) -> VerifiedResume:
    readable = await conversation_run_service.readable_conversation_run_ids(
        db, conversation, context
    )
    parent = await conversation_run_service.get_latest_interrupted_run(
        db, conversation_id=conversation.id, context=context, readable_run_ids=readable
    )
    if parent is None:
        raise _stale()
    checkpoint = await read_pending_checkpoint(
        db, conversation=conversation, context=context, parent_run=parent
    )
    if checkpoint is None or not checkpoint.interrupts:
        raise _stale()
    slots = {item["id"]: item for item in checkpoint.interrupts}
    answers = {entry.interrupt_id: entry for entry in entries}
    if len(answers) != len(entries) or answers.keys() != slots.keys():
        raise _stale()

    verified_legacy_ids = await require_pending_child_authority(
        db, conversation=conversation, context=context, parent_run=parent, pending=checkpoint
    )

    # The standard requires one answer for each open interrupt. Use native order and
    # native namespaces for reproducible digests and for child graph dispatch.
    submitted = tuple(
        SubmittedInterruptResponse(
            ident,
            tuple(slot["ns"]),
            checkpoint_response(
                answers[ident], value=slot["value"], native_kind=checkpoint.native_kinds.get(ident)
            ),
        )
        for ident, slot in slots.items()
    )
    responses = {item.interrupt_id: item.response for item in submitted}
    rows, reconstructed = await _lock_approval_rows(
        db, parent, conversation, slots, context, checkpoint, verified_legacy_ids
    )
    bound: dict[UUID, dict[str, UUID]] = {}
    for row in rows:
        if row.run_id is None:
            raise _stale()
        bound.setdefault(row.run_id, {})[row.interrupt_id] = row.id
        decisions = decision_types(responses[row.interrupt_id])
        if not isinstance(row.actions, list) or len(row.actions) != len(decisions):
            raise _stale()
        for action, choice in zip(row.actions, decisions, strict=True):
            if (
                not isinstance(action, Mapping)
                or not isinstance(action.get("allowed_decisions"), list)
                or (choice not in action["allowed_decisions"] and row.id not in reconstructed)
            ):
                raise ValidationError(
                    INVALID, "The recorded approval does not permit this decision"
                )
    stamp = approvals.now()
    expired = frozenset(
        row.interrupt_id for row in rows if approvals.aware(row.expires_at) <= stamp
    )
    if any(answers[ident].status != "cancelled" for ident in expired):
        raise _stale()
    # Scope processing mutates the decision dictionaries in-place before dispatch.
    # Keep both native representations bound to exactly the restored response objects.
    representative = (
        parent.interrupt_id if parent.interrupt_id in slots else submitted[0].interrupt_id
    )
    resume = ResumePayload(responses, representative, submitted)
    return VerifiedResume(
        parent,
        checkpoint.checkpoint_id,
        resume,
        checkpoint.interrupts,
        expired,
        bound,
        reconstructed,
    )


def edited_actions_digest(response: Mapping[str, Any]) -> str | None:
    """Digest only accepted replacements, preserving action order without storing plaintext."""
    edits = [
        decision["edited_action"]
        for decision in response["decisions"]
        if decision["type"] == "edit"
    ]
    return approvals.digest(edits) if edits else None


async def claim_verified_resume(
    db: AsyncSession, *, verified: VerifiedResume, actor_id: UUID, resumed_run_id: UUID
) -> None:
    """Claim decisions together with their child run, receipt and session consent."""
    if actor_id != verified.parent_run.user_id:
        raise _stale()
    # Historical transports could answer just one parallel sibling. The waiting
    # sibling may therefore have an approval on both its original and current run.
    # Both rows represent one decision, under the earliest of their deadlines.
    by_run = {**verified.approval_rows_by_run}
    by_run.setdefault(verified.parent_run.id, {})
    for run_id, bound in by_run.items():
        keys = verified.input_payload.keys() if run_id == verified.parent_run.id else bound.keys()
        for expiry in (False, True):
            responses = {
                ident: verified.input_payload[ident]
                for ident in keys
                if (ident in verified.expired_abandonments) == expiry
            }
            if not responses:
                continue
            types = {ident: decision_types(value) for ident, value in responses.items()}
            if expiry and any(set(choices) != {"reject"} for choices in types.values()):
                raise _stale()
            edited = {
                ident: digest
                for ident, response in responses.items()
                if (digest := edited_actions_digest(response)) is not None
            }
            try:
                claimed = await approvals.claim(
                    db,
                    approvals.Claim(
                        run_id=run_id,
                        decisions=types,
                        channel="expiry" if expiry else "chat",
                        actor_id=actor_id,
                        resumed_run_id=resumed_run_id,
                        edited_digests=edited,
                    ),
                )
            except approvals.ApprovalStale as exc:
                raise _stale() from exc
            expected = {ident: bound[ident] for ident in responses if ident in bound}
            if {row.interrupt_id: row.id for row in claimed} != expected:
                raise _stale()
            for row in claimed:
                if row.id in verified.reconstructed_legacy_rows:
                    # Record exactly what the native checkpoint permitted and the
                    # owner decided. The row has already been consumed by this child.
                    row.actions = [
                        {**action, "allowed_decisions": [choice]}
                        for action, choice in zip(row.actions, types[row.interrupt_id], strict=True)
                    ]


__all__ = [
    "PendingCheckpoint",
    "VerifiedResume",
    "claim_verified_resume",
    "edited_actions_digest",
    "read_pending_checkpoint",
    "verify_ag_ui_resume",
]
