"""Associate a selected root transcript with its currently readable physical runs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import batched
from uuid import UUID

from sqlalchemy import and_, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import raiseload

from app.agent_runtime.message_utils import parse_msg_id
from app.authz.conversation_run_scope import private_run_predicate
from app.authz.conversation_scope import require_private_conversation
from app.authz.org_context import OrgContext
from app.error_codes import resume_not_found
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_commands import load_run_identities
from app.services.ag_ui_snapshot_history import SnapshotHistory
from app.services.conversation_run_message_links import RunMessageLinkQuery, list_run_message_links
from app.services.independent_job_runs import readable_independent_child_run_ids

_PAGE_SIZE = 128


@dataclass(frozen=True, slots=True)
class SnapshotRuns:
    candidates: list[ConversationRun]
    message_runs: dict[str, UUID]
    public_ids: dict[UUID, str]
    branch_verified: set[UUID]
    independent: bool


def _checkpoint_owners(
    history: SnapshotHistory, conversation_id: UUID, requested: set[str]
) -> tuple[dict[str, UUID], set[str]]:
    owners: dict[str, UUID] = {}
    outputs: set[str] = set()
    seen: set[str] = set()
    duplicates: set[str] = set()
    for ref in history.messages:
        message = ref.message
        if isinstance(message, dict):
            raw_id = message.get("id")
            role = message.get("type") or message.get("role")
        else:
            raw_id = message.id
            role = message.type
        wire_id = (
            raw_id
            if isinstance(raw_id, str) and raw_id
            else str(parse_msg_id(None, conversation_id, ref.original_index))
        )
        if wire_id not in requested:
            continue
        if wire_id in seen:
            duplicates.add(wire_id)
        seen.add(wire_id)
        if role in ("ai", "assistant", "tool"):
            outputs.add(wire_id)
        if ref.checkpoint_id is not None and ref.checkpoint_id in history.checkpoint_run_ids:
            owners[wire_id] = history.checkpoint_run_ids[ref.checkpoint_id]
    return {
        key: owner for key, owner in owners.items() if key not in duplicates
    }, outputs - duplicates


async def _checkpoint_links(
    db: AsyncSession, conversation: Conversation, context: OrgContext, outputs: set[str]
) -> dict[str, UUID]:
    links: dict[str, UUID] = {}
    for page in batched(outputs, _PAGE_SIZE):
        rows = await list_run_message_links(db, RunMessageLinkQuery(conversation.id, context, page))
        links.update((item.message_id, item.run_id) for item in rows)
    return links


async def _runs_with_identities(
    db: AsyncSession, conversation: Conversation, context: OrgContext, independent: bool
) -> tuple[list[ConversationRun], dict[UUID, tuple[str, str | None]]]:
    # Legacy runs may predate organization columns. Their owned parent conversation grants
    # access only to wholly unattributed rows, never a row explicitly stamped for another org.
    base = (
        select(ConversationRun)
        .where(
            ConversationRun.conversation_id == conversation.id,
            private_run_predicate(context),
            or_(
                and_(
                    ConversationRun.tenant_id == context.tenant_id,
                    ConversationRun.org_id == context.org_id,
                ),
                and_(ConversationRun.tenant_id.is_(None), ConversationRun.org_id.is_(None)),
            ),
        )
        .options(raiseload(ConversationRun.metrics))
        .order_by(ConversationRun.created_at, ConversationRun.id)
        .limit(_PAGE_SIZE)
        .execution_options(autoflush=False)
    )
    readable: list[ConversationRun] = []
    identities: dict[UUID, tuple[str, str | None]] = {}
    stmt = base
    while True:
        page = list(await db.scalars(stmt))
        if not page:
            break
        approved = (
            await readable_independent_child_run_ids(
                db, conversation_id=conversation.id, run_ids=[r.id for r in page], context=context
            )
            if independent
            else None
        )
        rows = [r for r in page if approved is None or r.id in approved]
        readable.extend(rows)
        if rows:
            identities.update(
                await load_run_identities(db, context, conversation.id, [r.id for r in rows])
            )
        if len(page) < _PAGE_SIZE:
            break
        last = page[-1]
        stmt = base.where(
            tuple_(ConversationRun.created_at, ConversationRun.id) > (last.created_at, last.id)
        )
    return readable, identities


async def snapshot_runs(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    history: SnapshotHistory,
    message_ids: Sequence[str],
    *,
    run_id: UUID | None = None,
) -> SnapshotRuns:
    """Return chronological candidate journals and physical owners of visible root messages.

    An explicitly requested run still needs membership in this branch. Selected root stamps
    prove committed output. A command's admission at a shared fork point only proves where
    it began, and can include output from either continuation.
    """
    with db.no_autoflush:
        conversation = await require_private_conversation(db, conversation.id, context)
        independent = conversation.source == INDEPENDENT_JOB_SOURCE
        runs, identities = await _runs_with_identities(db, conversation, context, independent)
        scoped = {run.id for run in runs}
        if run_id is not None and run_id not in scoped:
            raise resume_not_found()
        requested = set(message_ids)
        owners, outputs = _checkpoint_owners(history, conversation.id, requested)
        if history.source == "legacy" and not independent:
            outputs = requested
        db_links = await _checkpoint_links(db, conversation, context, outputs - owners.keys())

    selected = set(history.checkpoint_run_ids.values())
    lineage = set(history.lineage_checkpoint_ids)
    candidates: list[ConversationRun] = []
    verified: set[UUID] = set()
    readable_origins: set[UUID] = set()
    for run in runs:
        metadata = run.metadata_json
        if metadata is not None and not isinstance(metadata, dict):
            continue
        stored = metadata.get("checkpoint_id") if metadata else None
        receipt = identities.get(run.id)
        pinned = receipt[1] if receipt else None
        if stored is not None and pinned is not None and stored != pinned:
            continue
        origin = pinned if pinned is not None else stored
        if origin is not None and (not isinstance(origin, str) or origin not in lineage):
            continue
        readable_origins.add(run.id)
        if run.id in history.other_branch_run_ids:
            # A partial run on the common prefix can have a journal suffix on another leaf.
            # Its committed messages may still have an owner; never append its whole journal.
            continue
        if (
            not history.has_branches
            or run.id in selected
            or (origin is not None and origin == history.checkpoint_id)
        ):
            verified.add(run.id)
        candidates.append(run)

    visible_owners = {
        wire_id: physical_id
        for wire_id, physical_id in {**db_links, **owners}.items()
        if physical_id in readable_origins
        and (physical_id not in history.other_branch_run_ids or wire_id in owners)
    }
    # UUID fallback applies only after the real scoped row (and independent lineage) is proven.
    included = {r.id for r in candidates} | set(visible_owners.values())
    public_ids = {key: identities[key][0] if key in identities else str(key) for key in included}
    return SnapshotRuns(candidates, visible_owners, public_ids, verified, independent)
