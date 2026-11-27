"""Transactional AG-UI command identities and read-only public/physical run lookups."""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import Nullable, Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_agent_checks import require_agent_run
from app.authz.conversation_input_scope import owned_input_predicate
from app.authz.conversation_scope import can_read_conversation_run_id, load_private_conversation
from app.authz.org_context import OrgContext
from app.authz.organization_execution_proof import ExecutionTransactionProof, live_proof
from app.authz.scoping import private_scope_predicate
from app.error_codes import conversation_not_found
from app.exceptions import AppError, ConflictError, ValidationError
from app.models.ag_ui_command_receipt import AgUiCommandReceipt as Receipt
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services.chat.chat_execution_preparation import require_executable_conversation
from app.services.conversation_run_service import get_run_for_user

PROJECTION_VERSION = "1"


@dataclass(frozen=True, slots=True)
class AgUiRunBinding:
    public_run_id: str
    run_id: UUID | None
    input_id: UUID | None
    input_status: str | None
    position: int | None
    projection_version: str
    checkpoint_id: str | None
    parent_public_run_id: str | None
    revision: int | None = None


@dataclass(frozen=True, slots=True)
class CommandAdmission:
    conversation_id: UUID
    public_run_id: str
    request_digest: str
    existing: AgUiRunBinding | None
    context: OrgContext = field(repr=False)
    proof: ExecutionTransactionProof = field(repr=False)


def _bindings(
    context: OrgContext, conversation_id: UUID
) -> Select[tuple[Receipt, UUID | None, str | None, int | None, int | None]]:
    # Queue inputs acquire their concrete run on claim. Only join a target belonging
    # to this receipt's owner and conversation, including when old data is malformed.
    queued = ConversationRunInput
    physical = ConversationRun
    return (
        select(
            Receipt,
            Nullable(physical.id),
            Nullable(queued.status),
            Nullable(queued.position),
            Nullable(queued.revision),
        )
        .outerjoin(
            queued,
            and_(
                queued.id == Receipt.input_id,
                queued.conversation_id == Receipt.conversation_id,
                owned_input_predicate(context),
            ),
        )
        .outerjoin(
            physical,
            and_(
                physical.id == func.coalesce(Receipt.run_id, queued.run_id),
                physical.conversation_id == Receipt.conversation_id,
                private_scope_predicate(physical, context),
            ),
        )
        .where(
            Receipt.conversation_id == conversation_id,
            private_scope_predicate(Receipt, context),
            or_(
                and_(Receipt.run_id.is_not(None), physical.id.is_not(None)),
                and_(
                    queued.id.is_not(None),
                    or_(queued.run_id.is_(None), physical.id.is_not(None)),
                ),
            ),
        )
        .execution_options(autoflush=False)
    )


async def _readable_conversation(
    db: AsyncSession, context: OrgContext, conversation_id: UUID
) -> Conversation | None:
    try:
        return await load_private_conversation(db, conversation_id, context)
    except AppError as exc:
        if exc.status in {403, 404}:
            return None
        raise


async def _binding(
    db: AsyncSession, context: OrgContext, conversation: Conversation, public_run_id: str
) -> AgUiRunBinding | None:
    stmt = _bindings(context, conversation.id).where(Receipt.public_run_id == public_run_id)
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        return None
    receipt, run_id, status, position, revision = row
    if not await can_read_conversation_run_id(db, conversation, run_id, context):
        return None
    return AgUiRunBinding(
        public_run_id=receipt.public_run_id,
        run_id=run_id,
        input_id=receipt.input_id,
        input_status=status,
        position=position,
        projection_version=receipt.projection_version,
        checkpoint_id=receipt.checkpoint_id,
        parent_public_run_id=receipt.parent_public_run_id,
        revision=revision,
    )


async def load_run_binding(
    db: AsyncSession, context: OrgContext, conversation_id: UUID, public_run_id: str
) -> AgUiRunBinding | None:
    """Resolve the customer's run identifier without preparing or changing a run."""
    conversation = await _readable_conversation(db, context, conversation_id)
    if conversation is None:
        return None
    return await _binding(db, context, conversation, public_run_id)


async def load_run_identities(
    db: AsyncSession, context: OrgContext, conversation_id: UUID, run_ids: Collection[UUID]
) -> dict[UUID, tuple[str, str | None]]:
    """Resolve public IDs and their admission checkpoints for a page of physical runs."""
    if not run_ids:
        return {}
    conversation = await _readable_conversation(db, context, conversation_id)
    if conversation is None:
        return {}
    if conversation.source == INDEPENDENT_JOB_SOURCE:
        from app.services.independent_job_runs import readable_independent_child_run_ids

        run_ids = await readable_independent_child_run_ids(
            db, conversation_id=conversation_id, run_ids=run_ids, context=context
        )
        if not run_ids:
            return {}
    stmt = (
        _bindings(context, conversation.id)
        .with_only_columns(
            ConversationRun.id,
            Receipt.public_run_id,
            Receipt.checkpoint_id,
            maintain_column_froms=True,
        )
        .where(ConversationRun.id.in_(set(run_ids)))
    )
    return {
        physical: (public, checkpoint) for physical, public, checkpoint in await db.execute(stmt)
    }


async def load_public_run_ids(
    db: AsyncSession, context: OrgContext, conversation_id: UUID, run_ids: Collection[UUID]
) -> dict[UUID, str]:
    identities = await load_run_identities(db, context, conversation_id, run_ids)
    return {physical: public for physical, (public, _) in identities.items()}


async def load_public_run_id(
    db: AsyncSession, context: OrgContext, conversation_id: UUID, run_id: UUID
) -> str | None:
    return (await load_public_run_ids(db, context, conversation_id, (run_id,))).get(run_id)


async def find_or_reserve(
    db: AsyncSession,
    context: OrgContext,
    conversation_id: UUID,
    public_run_id: str,
    request_digest: str,
) -> CommandAdmission:
    """Hold the normal execution locks until acceptance and binding commit together."""
    if not 1 <= len(public_run_id) <= 200 or "\x00" in public_run_id:
        raise ValidationError("AG_UI_INVALID_RUN_ID", "Invalid public run identifier")
    if re.fullmatch("[0-9a-f]{64}", request_digest) is None:
        raise ValueError("Expected a SHA-256 request digest")
    agent_id = await require_executable_conversation(db, conversation_id, context)
    await require_agent_run(context, agent_id)
    proof = live_proof(db)
    if proof is None or not proof.covers_conversation(conversation_id):
        raise RuntimeError("Command admission requires the conversation execution lock")
    conversation = await _readable_conversation(db, context, conversation_id)
    if conversation is None:
        raise conversation_not_found()
    existing = None
    stored_digest = await db.scalar(
        select(Receipt.request_digest).where(
            Receipt.conversation_id == conversation_id,
            Receipt.public_run_id == public_run_id,
            private_scope_predicate(Receipt, context),
        )
    )
    if stored_digest is not None:
        existing = await _binding(db, context, conversation, public_run_id)
        if existing is None:
            raise conversation_not_found()
        if stored_digest != request_digest:
            raise ConflictError("AG_UI_RUN_ID_CONFLICT", "The run ID belongs to another request")
    else:
        # Existing clients bookmarked physical UUIDs before public run IDs existed.
        try:
            native_id = UUID(public_run_id)
        except ValueError:
            native_id = None
        if native_id is not None:
            legacy = await get_run_for_user(
                db, conversation_id=conversation.id, run_id=native_id, context=context
            )
            if legacy is not None and await can_read_conversation_run_id(
                db, conversation, native_id, context
            ):
                raise ConflictError("AG_UI_RUN_ID_CONFLICT", "The run ID is already in use")
    return CommandAdmission(
        conversation_id, public_run_id, request_digest, existing, context, proof
    )


async def bind_command(
    db: AsyncSession,
    admission: CommandAdmission,
    *,
    run_id: UUID | None = None,
    input_id: UUID | None = None,
    checkpoint_id: str | None = None,
    parent_public_run_id: str | None = None,
    delivery_encrypted: str | None = None,
) -> AgUiRunBinding:
    """Bind the accepted input or physical run before its owning transaction commits."""
    admission.proof.require_live(db)
    if admission.existing is not None:
        raise ValueError("This public run is already bound")
    if (input_id is None) == (run_id is None) or (
        delivery_encrypted is not None and run_id is None
    ):
        raise ValueError("Exactly one accepted target is required; only runs can carry delivery")
    context = admission.context
    conversation = await _readable_conversation(db, context, admission.conversation_id)
    if conversation is None:
        raise conversation_not_found()
    physical_id = run_id
    if physical_id is not None:
        valid_target = await db.scalar(
            select(ConversationRun.id).where(
                ConversationRun.id == run_id,
                ConversationRun.conversation_id == conversation.id,
                private_scope_predicate(ConversationRun, context),
            )
        )
        if valid_target is None:
            raise conversation_not_found()
    else:
        queued = (
            await db.execute(
                select(ConversationRunInput.run_id).where(
                    ConversationRunInput.id == input_id,
                    ConversationRunInput.conversation_id == conversation.id,
                    owned_input_predicate(context),
                )
            )
        ).one_or_none()
        if queued is None:
            raise conversation_not_found()
        physical_id = queued[0]
    if not await can_read_conversation_run_id(db, conversation, physical_id, context):
        raise conversation_not_found()
    targets = []
    if input_id is not None:
        targets.append(Receipt.input_id == input_id)
    if physical_id is not None:
        targets.append(ConversationRun.id == physical_id)
    already_bound = (
        _bindings(context, conversation.id)
        .with_only_columns(Receipt.public_run_id, maintain_column_froms=True)
        .where(or_(*targets))
    )
    if await db.scalar(already_bound) is not None:
        raise ConflictError("AG_UI_TARGET_ALREADY_BOUND", "The target already has a public run ID")
    db.add(
        Receipt(
            tenant_id=context.tenant_id,
            org_id=context.org_id,
            user_id=context.user_id,
            conversation_id=conversation.id,
            public_run_id=admission.public_run_id,
            request_digest=admission.request_digest,
            run_id=run_id,
            input_id=input_id,
            projection_version=PROJECTION_VERSION,
            checkpoint_id=checkpoint_id,
            parent_public_run_id=parent_public_run_id,
            delivery_encrypted=delivery_encrypted,
        )
    )
    await db.flush()
    binding = await _binding(db, context, conversation, admission.public_run_id)
    if binding is None:
        raise conversation_not_found()
    return binding
