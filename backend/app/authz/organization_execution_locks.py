"""Claimed, cursor-ordered lock statements for execution proofs (internal I9).

Every canonical execution lock taken after the gateway prefix goes through
these helpers so the tracker sees a one-use claim and the proof's cursor
enforces class order (held rows are reused without SQL).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.execution_lock_cursor import ExecutionLockCursor, ExecutionProofError, LockClass
from app.authz.execution_transaction_tracker import inserted_in_transaction, issue_claim
from app.authz.org_context import OrgContext
from app.authz.organization_execution_proof import (
    ExecutionTransactionProof,
    live_proof,
)
from app.authz.scoping import private_scope_predicate
from app.models.conversation import Conversation


async def lock_row[T](
    db: AsyncSession,
    cursor: ExecutionLockCursor,
    lock_class: LockClass,
    resource_id: UUID,
    statement: Select[tuple[T]],
) -> T | None:
    """Run one claimed locking statement in cursor order; held rows skip SQL."""

    if not cursor.admit(lock_class, resource_id):
        return cursor.held_row(lock_class, resource_id)
    row = await db.scalar(
        statement.execution_options(populate_existing=True, autoflush=False, **issue_claim(db))
    )
    if row is not None:
        cursor.record(lock_class, resource_id, row)
    return row


async def lock_query[T](
    db: AsyncSession,
    lock_class: LockClass,
    statement: Select[tuple[T]],
) -> T | None:
    """Run one locking query whose row id is unknown in advance (e.g. SKIP LOCKED).

    Inside a live proof the statement carries a claim and its row is recorded
    in cursor order; without a proof it runs unchanged (nothing to police).
    """

    rows = await lock_many(db, lock_class, statement)
    return rows[0] if rows else None


async def lock_many[T](
    db: AsyncSession,
    lock_class: LockClass,
    statement: Select[tuple[T]],
) -> list[T]:
    proof = live_proof(db)
    if proof is None:
        return list((await db.scalars(statement)).all())
    position = proof.cursor.position
    if position is not None and lock_class < position and lock_class < LockClass.RUN:
        raise ExecutionProofError("execution_scope_expansion_denied")
    rows = list((await db.scalars(statement.execution_options(**issue_claim(db)))).all())
    for row in rows:
        resource_id = row.id  # type: ignore[attr-defined]
        if proof.cursor.admit(lock_class, resource_id):
            proof.cursor.record(lock_class, resource_id, row)
    return rows


async def extend_proof_conversation(
    db: AsyncSession, proof: ExecutionTransactionProof, context: OrgContext, conversation_id: UUID
) -> Conversation | None:
    """Add one Conversation to the proof's batch in cursor order (later classes deny)."""

    proof.require_live(db)
    if proof.covers_conversation(conversation_id):
        return held_conversation(db, conversation_id)
    if proof.archived_bypass:
        # An archived-org proof is bound to its lease's exact Conversation.
        raise ExecutionProofError("execution_scope_expansion_denied")
    if inserted_in_transaction(db, Conversation.__tablename__, conversation_id):
        # Created by this transaction: no other transaction can hold it, so it
        # joins as exact owned state without a lock or cursor movement.
        owned = await db.get(Conversation, conversation_id)
        if owned is None or owned.user_id != context.user_id:
            return None
        proof.cursor.register_owned(LockClass.CONVERSATION, conversation_id, owned)
        proof.conversation_ids = (*proof.conversation_ids, conversation_id)
        return owned
    row = await lock_row(
        db,
        proof.cursor,
        LockClass.CONVERSATION,
        conversation_id,
        select(Conversation)
        .where(Conversation.id == conversation_id, private_scope_predicate(Conversation, context))
        .with_for_update(of=Conversation),
    )
    if row is not None:
        proof.conversation_ids = tuple(
            sorted((*proof.conversation_ids, conversation_id), key=lambda value: value.bytes)
        )
    return row


def held_conversation(db: AsyncSession, conversation_id: UUID) -> Conversation | None:
    """Return the live proof's locked Conversation row (no SQL), if it holds one."""

    proof = live_proof(db)
    if proof is None:
        return None
    row = proof.cursor.held_row(LockClass.CONVERSATION, conversation_id)
    return row if isinstance(row, Conversation) else None
