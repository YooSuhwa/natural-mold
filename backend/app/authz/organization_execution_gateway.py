"""Canonical organization execution gateway (parent Task 10, internal I9).

Every execution entry acquires, before any lower row lock:
``Tenant -> Organization -> owner TenantMember -> owner OrganizationMember ->
User KEY SHARE -> sorted Agent lifecycle -> sorted declared Conversation batch``
and freshly requires an active Tenant, active owner memberships and an active
user. An archived Organization is admitted only for an exact live prepared
lease. The result is a transaction-bound :class:`ExecutionTransactionProof`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.prepared_run_registry import (
    PreparedRunLeaseError,
    get_prepared_run_registry,
)
from app.agent_runtime.runtime_config import PreparedRunLease
from app.authz.execution_lock_cursor import ExecutionLockCursor, ExecutionProofError, LockClass
from app.authz.execution_transaction_tracker import (
    claimed_locks,
    current_state,
    inserted_in_transaction,
    install_execution_transaction_tracker,
)
from app.authz.org_context import OrgContext
from app.authz.organization_execution_locks import (  # noqa: E402 - re-exported
    extend_proof_conversation,
    held_conversation,
    lock_many,
    lock_query,
    lock_row,
)
from app.authz.organization_execution_prefix import (  # noqa: E402
    lock_execution_prefix,
    unavailable,
)
from app.authz.organization_execution_proof import (
    PROOF_REQUIRED,
    ExecutionScope,
    ExecutionTransactionProof,
    live_proof,
)
from app.authz.scoping import private_scope_predicate
from app.models.conversation import Conversation
from app.services import agent_lifecycle_locks

install_execution_transaction_tracker()
__all__ = [
    "acquire_execution_proof",
    "extend_proof_conversation",
    "held_conversation",
    "lock_many",
    "lock_query",
    "lock_row",
    "require_execution_prefix",
]


_TREE_ATTEMPTS = 3


async def _lock_agent_tree(
    db: AsyncSession, cursor: ExecutionLockCursor, root_agent_id: UUID
) -> tuple[UUID, ...]:
    """Lock a stable Agent tree in ascending lifecycle order, re-reading on drift.

    A concurrent link edit re-reads the tree (bounded) instead of failing; newly
    discovered members must still sort after every held lifecycle lock.
    """
    for _attempt in range(_TREE_ATTEMPTS):
        before = await agent_lifecycle_locks.read_agent_tree(db, root_agent_id)
        agent_ids = sorted(before.agent_ids or (root_agent_id,), key=lambda value: value.bytes)
        missing = [item for item in agent_ids if not cursor.holds(LockClass.AGENT_LIFECYCLE, item)]
        for agent_id in missing:
            cursor.admit(LockClass.AGENT_LIFECYCLE, agent_id)
        if missing:
            with claimed_locks(db):
                await agent_lifecycle_locks.acquire_agent_lifecycle_locks(db, missing)
            for agent_id in missing:
                cursor.record(LockClass.AGENT_LIFECYCLE, agent_id, agent_id)
        after = await agent_lifecycle_locks.read_agent_tree(db, root_agent_id)
        if after == before:
            return tuple(agent_ids)
    raise ExecutionProofError("agent_tree_changed")


async def _extend_agent_tree(
    db: AsyncSession, proof: ExecutionTransactionProof, root_agent_id: UUID
) -> None:
    """Add an Agent tree to a live proof while the cursor is still before Conversations.

    Agents this transaction inserted join as owned state; others are locked in
    ascending lifecycle order (a lower UUID than one already held is denied).
    """
    proof.require_live(db)
    if proof.archived_bypass:
        raise ExecutionProofError("execution_scope_expansion_denied")
    snapshot = await agent_lifecycle_locks.read_agent_tree(db, root_agent_id)
    tree = sorted(snapshot.agent_ids or (root_agent_id,), key=lambda value: value.bytes)
    to_lock: list[UUID] = []
    for agent_id in tree:
        if proof.cursor.holds(LockClass.AGENT_LIFECYCLE, agent_id):
            continue
        if inserted_in_transaction(db, "agents", agent_id):
            proof.cursor.register_owned(LockClass.AGENT_LIFECYCLE, agent_id, agent_id)
            continue
        proof.cursor.admit(LockClass.AGENT_LIFECYCLE, agent_id)
        to_lock.append(agent_id)
    if to_lock:
        with claimed_locks(db):
            await agent_lifecycle_locks.acquire_agent_lifecycle_locks(db, to_lock)
        for agent_id in to_lock:
            proof.cursor.record(LockClass.AGENT_LIFECYCLE, agent_id, agent_id)
    proof.agent_ids = tuple(sorted({*proof.agent_ids, *tree}, key=lambda value: value.bytes))


async def _lock_conversations(
    db: AsyncSession,
    cursor: ExecutionLockCursor,
    context: OrgContext,
    conversation_ids: Sequence[UUID],
) -> tuple[UUID, ...]:
    ordered = tuple(sorted(set(conversation_ids), key=lambda value: value.bytes))
    locked: list[UUID] = []
    for conversation_id in ordered:
        row = await lock_row(
            db,
            cursor,
            LockClass.CONVERSATION,
            conversation_id,
            select(Conversation)
            .where(
                Conversation.id == conversation_id,
                private_scope_predicate(Conversation, context),
            )
            .with_for_update(of=Conversation),
        )
        if row is not None:
            locked.append(conversation_id)
    return tuple(locked)


def _lease_admits_archive(
    lease: PreparedRunLease | None,
    context: OrgContext,
    root_agent_id: UUID | None,
    conversation_ids: Sequence[UUID],
) -> bool:
    """Admit an archived Organization only for the exact run of a live lease."""
    if lease is None:
        return False
    binding = lease.binding
    if (binding.tenant_id, binding.org_id, binding.user_id) != (
        context.tenant_id,
        context.org_id,
        context.user_id,
    ):
        return False
    if root_agent_id is not None and root_agent_id != binding.agent_id:
        return False
    if any(item != binding.conversation_id for item in conversation_ids):
        return False
    try:
        get_prepared_run_registry().acquire(binding, token=lease.token, digest=lease.digest)
    except PreparedRunLeaseError:
        return False
    return True


async def acquire_execution_proof(
    db: AsyncSession,
    context: OrgContext,
    *,
    root_agent_id: UUID | None = None,
    conversation_ids: Sequence[UUID] = (),
    lease: PreparedRunLease | None = None,
) -> ExecutionTransactionProof:
    """Lock and revalidate the canonical prefix, or reuse this transaction's proof."""

    scope = ExecutionScope(context.tenant_id, context.org_id, context.user_id)
    existing = live_proof(db)
    if existing is not None:
        existing.require_scope(scope)
        if root_agent_id is not None and root_agent_id not in existing.agent_ids:
            with db.no_autoflush:
                await _extend_agent_tree(db, existing, root_agent_id)
        with db.no_autoflush:
            for conversation_id in sorted(conversation_ids, key=lambda value: value.bytes):
                await extend_proof_conversation(db, existing, context, conversation_id)
        return existing
    state = current_state(db)
    if state is None or state.lock_count:
        raise ExecutionProofError(PROOF_REQUIRED)
    with db.no_autoflush:
        return await _acquire(db, state, scope, context, root_agent_id, conversation_ids, lease)


async def _acquire(
    db: AsyncSession,
    state: Any,
    scope: ExecutionScope,
    context: OrgContext,
    root_agent_id: UUID | None,
    conversation_ids: Sequence[UUID],
    lease: PreparedRunLease | None,
) -> ExecutionTransactionProof:
    cursor = ExecutionLockCursor()
    organization = await lock_execution_prefix(db, cursor, context)
    archived_bypass = False
    if organization.status != "active":
        archived_bypass = organization.status == "archived" and _lease_admits_archive(
            lease, context, root_agent_id, conversation_ids
        )
        if not archived_bypass:
            raise unavailable()
    agent_ids = (
        await _lock_agent_tree(db, cursor, root_agent_id) if root_agent_id is not None else ()
    )
    conversations = await _lock_conversations(db, cursor, context, conversation_ids)
    proof = ExecutionTransactionProof(
        session=db,
        sync_session_id=id(db.sync_session),
        state=state,
        generation=state.generation,
        savepoint_generation=state.savepoint_generation,
        nonce=state.nonce,
        scope=scope,
        agent_ids=agent_ids,
        conversation_ids=conversations,
        cursor=cursor,
        archived_bypass=archived_bypass,
    )
    state.proof = proof
    return proof


async def require_execution_prefix(db: AsyncSession, context: OrgContext) -> None:
    """Parent creation gate: lock and revalidate the prefix without issuing a proof.

    Agent creation ingresses call this before inserting an Agent. The claimed
    prefix locks keep a later execution proof in the same transaction legal
    (it re-locks the same rows from the top in canonical order).
    """

    if live_proof(db) is not None:
        await acquire_execution_proof(db, context)
        return
    state = current_state(db)
    if state is None or state.lock_count:
        raise ExecutionProofError(PROOF_REQUIRED)
    with db.no_autoflush:
        organization = await lock_execution_prefix(db, ExecutionLockCursor(), context)
    if organization.status != "active":
        raise unavailable()
