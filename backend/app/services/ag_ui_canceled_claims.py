"""Recover a journal after native cancel-before-start clears a claimed input's run ID."""

from collections import defaultdict
from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_input_scope import owned_input_predicate
from app.authz.org_context import OrgContext
from app.authz.scoping import private_scope_predicate
from app.models.ag_ui_command_receipt import AgUiCommandReceipt as Receipt
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput as Queued


async def canceled_input_candidates(
    db: AsyncSession, context: OrgContext, conversation_id: UUID, run_ids: Collection[UUID]
) -> set[UUID]:
    """Inspect only the requested physical runs before consulting canceled queue history."""
    refs = await db.scalars(
        select(ConversationRun.metadata_json["queue_input_id"].as_string())
        .where(
            ConversationRun.id.in_(run_ids),
            ConversationRun.conversation_id == conversation_id,
            private_scope_predicate(ConversationRun, context),
            ConversationRun.status == "canceled",
        )
        .execution_options(autoflush=False)
    )
    candidates: set[UUID] = set()
    for ref in refs:
        if isinstance(ref, str):
            try:
                candidates.add(UUID(ref))
            except ValueError:
                continue
    return candidates


async def canceled_claim_identities(
    db: AsyncSession, context: OrgContext, conversation_id: UUID, input_ids: Collection[UUID]
) -> dict[UUID, tuple[str, str | None]]:
    """Require both server-written queue markers and exactly one matching scoped run."""
    stmt = (
        select(Queued.id, Queued.client_request_id, Receipt.public_run_id, Receipt.checkpoint_id)
        .join(Receipt, Receipt.input_id == Queued.id)
        .where(
            Queued.id.in_(input_ids),
            Queued.conversation_id == conversation_id,
            owned_input_predicate(context),
            Queued.status == "canceled",
            Queued.run_id.is_(None),
            Queued.claimed_at.is_not(None),
            Receipt.conversation_id == conversation_id,
            private_scope_predicate(Receipt, context),
        )
        .execution_options(autoflush=False)
    )
    receipts = {
        str(input_id): (request, public, checkpoint)
        for input_id, request, public, checkpoint in await db.execute(stmt)
    }
    if not receipts:
        return {}
    input_ref = ConversationRun.metadata_json["queue_input_id"].as_string()
    request_ref = ConversationRun.metadata_json["client_request_id"].as_string()
    history = (
        select(ConversationRun.id, input_ref, request_ref)
        .where(
            ConversationRun.conversation_id == conversation_id,
            private_scope_predicate(ConversationRun, context),
            ConversationRun.status == "canceled",
            input_ref.in_(receipts),
        )
        .execution_options(autoflush=False)
    )
    matches: dict[str, list[UUID]] = defaultdict(list)
    for run_id, input_id, request in await db.execute(history):
        if receipts[input_id][0] == request:
            matches[input_id].append(run_id)
    return {
        physicals[0]: (receipts[input_id][1], receipts[input_id][2])
        for input_id, physicals in matches.items()
        if len(physicals) == 1
    }
