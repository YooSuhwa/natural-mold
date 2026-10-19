"""Transaction-bound execution proof (parent Task 10, internal I9).

A proof binds the exact ``AsyncSession``, its sync session, the root
transaction state (generation, savepoint generation, opaque nonce), the
tenant/org/user scope, the sorted Agent lifecycle IDs, the declared sorted
Conversation batch and the lock cursor. It is valid only while that exact root
transaction is live with no savepoint change and no untracked lower lock.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.execution_lock_cursor import ExecutionLockCursor, ExecutionProofError
from app.authz.execution_transaction_tracker import TransactionState, current_state
from app.authz.org_context import OrgContext

PROOF_REQUIRED = "execution_proof_required"
PROOF_STALE = "execution_proof_stale"
UNTRACKED_LOWER_LOCK = "untracked_lower_lock"


@dataclass(frozen=True, slots=True)
class ExecutionScope:
    tenant_id: UUID
    org_id: UUID
    user_id: UUID


@dataclass(eq=False, slots=True)
class ExecutionTransactionProof:
    session: AsyncSession
    sync_session_id: int
    state: TransactionState
    generation: int
    savepoint_generation: int
    nonce: str
    scope: ExecutionScope
    agent_ids: tuple[UUID, ...]
    conversation_ids: tuple[UUID, ...]
    cursor: ExecutionLockCursor
    archived_bypass: bool = False

    def require_live(self, db: AsyncSession) -> None:
        state = current_state(db)
        if (
            state is None
            or db is not self.session
            or id(db.sync_session) != self.sync_session_id
            or state is not self.state
            or state.generation != self.generation
            or state.savepoint_generation != self.savepoint_generation
            or state.nonce != self.nonce
            or state.proof is not self
        ):
            raise ExecutionProofError(PROOF_STALE)
        if state.untracked_lower_lock:
            raise ExecutionProofError(UNTRACKED_LOWER_LOCK)

    def require_scope(self, scope: ExecutionScope) -> None:
        if scope != self.scope:
            raise ExecutionProofError("execution_scope_expansion_denied")

    def covers_conversation(self, conversation_id: UUID) -> bool:
        return conversation_id in self.conversation_ids


def execution_scope(context: OrgContext) -> ExecutionScope:
    return ExecutionScope(context.tenant_id, context.org_id, context.user_id)


def live_proof(db: AsyncSession) -> ExecutionTransactionProof | None:
    """Return the live proof bound to ``db``'s current root transaction, if any."""

    state = current_state(db)
    proof = None if state is None else state.proof
    if not isinstance(proof, ExecutionTransactionProof):
        return None
    proof.require_live(db)
    return proof
