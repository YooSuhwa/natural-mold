"""Transaction identity tracking for execution proofs (parent Task 10).

A once-installed set of SQLAlchemy ``Session`` event listeners keeps one
process-local state per root transaction. The state is replaced on root
creation and invalidated on root commit/rollback, nested (savepoint) creation
or end, and soft rollback, so a proof can never survive any of them. While a
proof is live, every ORM locking statement (``FOR UPDATE``/``FOR SHARE``/``KEY
SHARE``) must carry a one-use claim issued by the gateway or run inside a
gateway claim scope; any other locking statement marks the state
``untracked_lower_lock``.
"""

from __future__ import annotations

import itertools
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session, SessionTransaction
from sqlalchemy.sql.elements import TextClause

CLAIM_OPTION = "moldy_execution_lock_claim"
# Tables in the canonical execution lock order. Locks on other tables (skills,
# credentials, MCP, ...) are outside the inventory and are not policed here.
CANONICAL_LOCK_TABLES = frozenset(
    {
        "tenants",
        "organizations",
        "tenant_members",
        "organization_members",
        "users",
        "agents",
        "agent_triggers",
        "agent_deployments",
        "conversations",
        "conversation_runs",
        "conversation_run_inputs",
        "agent_api_runs",
        "agent_trigger_runs",
    }
)
_INFO_KEY = "moldy_execution_transaction"
_PENDING_LOCKS_KEY = "moldy_execution_pending_locks"
_generations = itertools.count(1)
_installed = False


@dataclass(eq=False, slots=True)
class TransactionState:
    root: SessionTransaction
    generation: int
    nonce: str = field(default_factory=lambda: secrets.token_hex(16))
    savepoint_generation: int = 0
    valid: bool = True
    proof: Any = None
    lock_count: int = 0
    untracked_lower_lock: bool = False
    claim_scope_depth: int = 0
    claims: set[str] = field(default_factory=set)
    inserted: set[tuple[str, Any]] = field(default_factory=set)


def _state(session: Session) -> TransactionState | None:
    state = session.info.get(_INFO_KEY)
    return state if isinstance(state, TransactionState) else None


def _after_transaction_create(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        state = TransactionState(transaction, next(_generations))
        # A locking statement that autobegins this transaction is counted
        # before the transaction (and its state) exists.
        state.lock_count = int(session.info.pop(_PENDING_LOCKS_KEY, 0))
        session.info[_INFO_KEY] = state
        return
    state = _state(session)
    if state is not None and transaction.nested:
        state.savepoint_generation += 1


def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    state = _state(session)
    if state is None:
        return
    if transaction is state.root:
        state.valid = False
        session.info.pop(_INFO_KEY, None)
    elif transaction.nested:
        state.savepoint_generation += 1


def _after_soft_rollback(session: Session, previous_transaction: SessionTransaction) -> None:
    state = _state(session)
    if state is not None:
        state.valid = False


def _after_flush(session: Session, flush_context: Any) -> None:
    state = _state(session)
    if state is None:
        return
    for instance in session.new:
        identity = getattr(instance, "id", None)
        if identity is not None:
            state.inserted.add((type(instance).__tablename__, identity))


def inserted_in_transaction(db: AsyncSession, table: str, identity: Any) -> bool:
    state = current_state(db)
    return state is not None and (table, identity) in state.inserted


def _is_locking(orm_execute_state: ORMExecuteState) -> bool:
    statement = orm_execute_state.statement
    if isinstance(statement, TextClause):
        # Agent lifecycle advisory locks are canonical-order locks too.
        return "pg_advisory_xact_lock" in statement.text
    if not orm_execute_state.is_select:
        return False
    if getattr(statement, "_for_update_arg", None) is None:
        return False
    froms = getattr(statement, "get_final_froms", lambda: ())()
    names = {getattr(item, "name", None) for item in froms}
    return bool(names & CANONICAL_LOCK_TABLES)


def _do_orm_execute(orm_execute_state: ORMExecuteState) -> None:
    if not _is_locking(orm_execute_state):
        return
    session = orm_execute_state.session
    state = _state(session)
    if state is None or session.get_transaction() is None:
        session.info[_PENDING_LOCKS_KEY] = int(session.info.get(_PENDING_LOCKS_KEY, 0)) + 1
        return
    claim = orm_execute_state.execution_options.get(CLAIM_OPTION)
    if isinstance(claim, str) and claim in state.claims:
        state.claims.discard(claim)
        return
    if state.claim_scope_depth > 0:
        return
    # Unclaimed canonical lock: blocks a later proof acquisition (wrong order)
    # and poisons a live proof.
    state.lock_count += 1
    if state.proof is not None:
        state.untracked_lower_lock = True


def install_execution_transaction_tracker() -> None:
    global _installed
    if _installed:
        return
    event.listen(Session, "after_transaction_create", _after_transaction_create)
    event.listen(Session, "after_transaction_end", _after_transaction_end)
    event.listen(Session, "after_soft_rollback", _after_soft_rollback)
    event.listen(Session, "do_orm_execute", _do_orm_execute)
    event.listen(Session, "after_flush", _after_flush)
    _installed = True


def current_state(db: AsyncSession) -> TransactionState | None:
    """Return the live root-transaction state (beginning one if needed)."""

    sync = db.sync_session
    transaction = sync.get_transaction()
    if transaction is None:
        sync.begin()
        transaction = sync.get_transaction()
    root = transaction
    while root is not None and root.parent is not None:
        root = root.parent
    if root is None:
        return None
    state = _state(sync)
    if state is None and _installed:
        state = TransactionState(root, next(_generations))
        sync.info[_INFO_KEY] = state
    if state is None or root is not state.root or not state.valid:
        return None
    return state


def issue_claim(db: AsyncSession) -> dict[str, Any]:
    """Return execution options carrying a one-use lock claim for this transaction."""

    state = current_state(db)
    if state is None:
        return {}
    token = secrets.token_hex(12)
    state.claims.add(token)
    return {CLAIM_OPTION: token}


@contextmanager
def claimed_locks(db: AsyncSession) -> Iterator[None]:
    """Admit locking statements issued by a reviewed helper inside the gateway."""

    state = current_state(db)
    if state is not None:
        state.claim_scope_depth += 1
    try:
        yield
    finally:
        if state is not None:
            state.claim_scope_depth -= 1
