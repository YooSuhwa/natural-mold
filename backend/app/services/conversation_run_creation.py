"""Conversation run creation and state machine extracted from ``conversation_run_service``.

Task 10 I4: ``create_run`` plus the run status vocabulary, transition, cancel-intent,
and heartbeat writes, moved unchanged.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.runtime_policy import RuntimePolicySnapshotError
from app.authz.conversation_run_scope import private_run_predicate
from app.authz.execution_lock_cursor import ExecutionProofError, LockClass
from app.authz.org_context import OrgContext
from app.authz.organization_execution_gateway import (
    acquire_execution_proof,
    extend_proof_conversation,
)
from app.authz.organization_execution_proof import (
    ExecutionTransactionProof,
    execution_scope,
    live_proof,
)
from app.authz.scoping import private_scope_predicate
from app.exceptions import AppError, ConflictError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.schemas.conversation_scope import ConversationPolicyTarget
from app.services.conversation_runtime_policy import (
    copy_snapshot_to_run,
    ensure_conversation_runtime_policy_from_locked_proof,
    require_parent_snapshot_match,
)
from app.tenancy.attribution import RunAttribution
from app.tenancy.limits import admit_run


def run_service_attr(name: str) -> Any:
    """Return ``conversation_run_service.<name>`` as currently bound."""

    from app.services import conversation_run_service

    return getattr(conversation_run_service, name)


RunSource = Literal["chat", "start", "edit", "regenerate", "resume"]
RunStatus = Literal[
    "queued",
    "running",
    "interrupted",
    "canceling",
    "canceled",
    "completed",
    "failed",
    "stale",
]

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "failed", "stale", "canceling"},
    "running": {"completed", "failed", "interrupted", "canceling", "stale"},
    "canceling": {"canceled", "failed", "stale"},
    "completed": set(),
    "failed": set(),
    "interrupted": set(),
    "canceled": set(),
    "stale": set(),
}


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail=message)


def _is_active_run_unique_violation(exc: IntegrityError) -> bool:
    text = f"{exc} {getattr(exc, 'orig', '')}".lower()
    return "uq_conversation_runs_active_conversation" in text or (
        "unique constraint failed" in text and "conversation_runs.conversation_id" in text
    )


async def create_run(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    agent_id: uuid.UUID,
    context: OrgContext,
    source: RunSource,
    input_preview: str | None,
    parent_run_id: uuid.UUID | None = None,
    interrupt_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    allow_legacy_resume: bool = False,
) -> ConversationRun:
    """Compatibility gateway for direct legacy callers (not a late fallback).

    The first call in a clean root transaction acquires the canonical prefix and
    locks the complete UUID-sorted batch of this user's conversations with the
    Agent before any Run access; later calls in the transaction reuse exactly
    those resources. A conversation outside the batch is denied, never added.
    """
    proof = live_proof(db)
    if proof is not None and not proof.covers_conversation(conversation_id):
        # create_run_from_locked_proof maps a missing row to 404, else denies.
        with contextlib.suppress(ExecutionProofError):
            await extend_proof_conversation(db, proof, context, conversation_id)
    if proof is None:
        batch = tuple(
            await db.scalars(
                select(Conversation.id).where(
                    Conversation.agent_id == agent_id,
                    private_scope_predicate(Conversation, context),
                )
            )
        )
        try:
            proof = await acquire_execution_proof(
                db, context, root_agent_id=agent_id, conversation_ids=batch
            )
        except AppError:
            raise HTTPException(status_code=404, detail="Conversation not found") from None
    return await create_run_from_locked_proof(
        db,
        proof,
        conversation_id=conversation_id,
        agent_id=agent_id,
        context=context,
        source=source,
        input_preview=input_preview,
        parent_run_id=parent_run_id,
        interrupt_id=interrupt_id,
        metadata=metadata,
        allow_legacy_resume=allow_legacy_resume,
    )


async def create_run_from_locked_proof(
    db: AsyncSession,
    proof: ExecutionTransactionProof,
    *,
    conversation_id: uuid.UUID,
    agent_id: uuid.UUID,
    context: OrgContext,
    source: RunSource,
    input_preview: str | None,
    parent_run_id: uuid.UUID | None = None,
    interrupt_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    allow_legacy_resume: bool = False,
    attribution: RunAttribution | None = None,
) -> ConversationRun:
    proof.require_live(db)
    proof.require_scope(execution_scope(context))
    if not proof.covers_conversation(conversation_id):
        visible = await db.scalar(
            select(Conversation.id)
            .where(
                Conversation.id == conversation_id,
                Conversation.agent_id == agent_id,
                private_scope_predicate(Conversation, context),
            )
            .execution_options(autoflush=False)
        )
        if visible is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        raise ExecutionProofError("execution_scope_expansion_denied")
    conversation = proof.cursor.held_row(LockClass.CONVERSATION, conversation_id)
    if not isinstance(conversation, Conversation) or conversation.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    try:
        _, _, runtime_policy = await ensure_conversation_runtime_policy_from_locked_proof(
            db, proof, ConversationPolicyTarget(conversation.id, context)
        )
    except RuntimePolicySnapshotError as exc:
        raise ConflictError(exc.code, exc.code) from None

    active = await run_service_attr("get_active_run")(
        db, conversation_id=conversation_id, context=context
    )
    if active is not None:
        raise _conflict("Conversation already has an active run")

    if source == "resume":
        if parent_run_id is None:
            if not allow_legacy_resume or not interrupt_id:
                raise _conflict("Resume run requires an interrupted parent run")
            existing_legacy_resume = await db.execute(
                select(ConversationRun)
                .where(
                    ConversationRun.conversation_id == conversation_id,
                    private_run_predicate(context),
                    ConversationRun.source == "resume",
                    ConversationRun.parent_run_id.is_(None),
                    ConversationRun.interrupt_id == interrupt_id,
                )
                .limit(1)
            )
            if existing_legacy_resume.scalar_one_or_none() is not None:
                raise _conflict("Legacy interrupted run was already resumed")
        else:
            latest = await run_service_attr("_latest_interrupted_run")(
                db,
                conversation_id=conversation_id,
                context=context,
            )
            if latest is None or latest.id != parent_run_id:
                raise _conflict("Resume run must target the latest interrupted run")
            if latest.interrupt_id and latest.interrupt_id != interrupt_id:
                raise _conflict("Resume interrupt id does not match the interrupted run")
            try:
                require_parent_snapshot_match(latest, runtime_policy)
            except RuntimePolicySnapshotError as exc:
                raise ConflictError(exc.code, exc.code) from None

    # H-06 (8.7): company runs/rate and spend limits refuse a new run (a resume continues).
    await admit_run(db, attribution, enforce=source != "resume")
    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=agent_id,
        user_id=context.user_id,
        parent_run_id=parent_run_id,
        source=source,
        status="queued",
        is_active=True,
        interrupt_id=interrupt_id if source == "resume" else None,
        input_preview=(input_preview[:500] if input_preview else None),
        metadata_json=metadata,
        # F-14 (11.10): scope always; the rest when the acceptance attributed the run.
        **(
            attribution.run_columns()
            if attribution is not None
            else {"org_id": context.org_id, "tenant_id": context.tenant_id}
        ),
    )
    copy_snapshot_to_run(run, runtime_policy)
    db.add(run)
    try:
        await db.flush()
    except IntegrityError as exc:
        if _is_active_run_unique_violation(exc):
            await db.rollback()
            raise _conflict("Conversation already has an active run") from exc
        raise
    return run


from app.services.conversation_run_state import (  # noqa: E402 - re-export after definitions
    heartbeat_run,
    request_cancel_run,
    transition_run,
)

__all__ = ["heartbeat_run", "request_cancel_run", "transition_run"]
