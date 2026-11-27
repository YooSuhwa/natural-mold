"""Recover one authorized run from the same committed frames used for reattachment."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_v1 import public_subagent_id
from app.agent_runtime.ag_ui_v1_activities import PUBLIC_ACTIVITY_TYPES
from app.agent_runtime.ag_ui_v1_children import (
    Namespace,
    journal_namespace,
    public_journal_namespace,
)
from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.agent_runtime.protocol_events import protocol_interrupts_from_event
from app.authz.org_context import OrgContext
from app.error_codes import resume_not_found
from app.exceptions import ConflictError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_journal import JournalPage, read_journal_page
from app.services.ag_ui_read_cursor import CursorScope
from app.services.ag_ui_read_pending import CurrentReadInterrupts, current_read_interrupts
from app.services.ag_ui_read_projection import CommittedProjection
from app.services.ag_ui_read_target import resolve_read_target, snapshot_required
from app.services.ag_ui_snapshot_projection import AgUiRunSnapshot, Row


@dataclass(slots=True)
class SnapshotRunReplay:
    visibility: ChildVisibility
    scope: CursorScope | None = None
    messages: list[Row] = field(default_factory=list)
    message_links: dict[str, Row] = field(default_factory=dict)
    root_issuances: dict[tuple[str | None, str], str] = field(default_factory=dict)
    subagents: list[Row] = field(default_factory=list)
    todos_by_owner: dict[str | None, list[Row]] = field(default_factory=dict)
    legacy_users: list[Row] = field(default_factory=list)
    child_namespaces: dict[Namespace, str] = field(default_factory=dict)
    pending_namespaces: dict[str, Namespace] = field(default_factory=dict)
    unresolved_pending_ids: set[str] = field(default_factory=set)
    cursor: str | None = None
    complete: bool = False


@dataclass
class _ObservedOwners:
    """Match observed namespaces to public children emitted by the authorized projector."""

    namespaces: set[Namespace] = field(default_factory=set)
    pending: dict[str, set[Namespace]] = field(default_factory=dict)

    def observe(self, page: JournalPage) -> None:
        for entry in page.entries:
            for event in entry.events:
                namespace = public_journal_namespace(event)
                if namespace is None:
                    continue
                if namespace:
                    self.namespaces.add(namespace)
                for interrupt in protocol_interrupts_from_event(event):
                    owner_ns = journal_namespace(interrupt["ns"])
                    if owner_ns is None:
                        continue
                    owner_ns = owner_ns or namespace
                    self.pending.setdefault(interrupt["id"], set()).add(owner_ns)
                    if owner_ns:
                        self.namespaces.add(owner_ns)

    def bind(self, result: SnapshotRunReplay, thread_id: str, handles: Mapping[str, str]) -> None:
        children = {row["subagentRunId"]: row for row in result.subagents}
        for ns in self.namespaces:
            public_id = public_subagent_id(thread_id, ns)
            child = children.get(public_id)
            if child is None:
                continue
            result.child_namespaces[ns] = public_id
            # The shared projector has already verified the durable parent call
            # and immutable namespace. A public control also needs a visible issuer.
            if (
                public_id in handles
                and child.get("parentToolCallId")
                and child.get("parentMessageId")
            ):
                child["childTaskId"] = handles[public_id]

        for interrupt_id, observed in self.pending.items():
            if len(observed) == 1 and (ns := next(iter(observed))) in result.child_namespaces:
                result.pending_namespaces[interrupt_id] = ns
            elif any(observed):
                result.unresolved_pending_ids.add(interrupt_id)


async def replay_snapshot_run(
    db: AsyncSession,
    *,
    conversation: Conversation,
    context: OrgContext,
    run: ConversationRun,
    public_id: str,
    parent_public_id: str | None,
    secret_values: Collection[str],
) -> SnapshotRunReplay:
    """Materialize a finite committed prefix for a single authorized physical/public pair."""
    target = await resolve_read_target(db, conversation, context, public_id)
    if (
        target.run is None
        or target.run.id != run.id
        or (parent_public_id is not None and parent_public_id != target.parent_public_id)
    ):
        raise resume_not_found()
    projection = CommittedProjection(target, secret_values)
    fold = AgUiRunSnapshot(public_activity_types=PUBLIC_ACTIVITY_TYPES, physical_run_id=str(run.id))
    owners = _ObservedOwners()
    result = SnapshotRunReplay(visibility=target.visibility, scope=target.scope)
    horizon: int | None = None
    while True:
        page = await read_journal_page(
            db,
            conversation=conversation,
            physical_run_id=run.id,
            after=projection.source_ordinal,
            secret_values=secret_values,
        )
        if horizon is None:
            horizon = page.high_water
        if page.gap or page.high_water < horizon:
            return SnapshotRunReplay(visibility=target.visibility, scope=target.scope)
        if page.high_water > horizon:
            # A worker appended during this snapshot. Its terminal state must
            # stay behind those later source rows as it does on the live stream.
            page = replace(
                page, entries=tuple(e for e in page.entries if e.ordinal <= horizon), has_more=True
            )
        current = await resolve_read_target(db, conversation, context, public_id)
        if current.scope != target.scope or current.visibility != target.visibility:
            raise snapshot_required()
        last_ordinal = page.entries[-1].ordinal if page.entries else projection.source_ordinal
        last = last_ordinal == horizon
        if (not last and not page.has_more) or (page.has_more and not page.entries):
            return SnapshotRunReplay(visibility=target.visibility, scope=target.scope)
        pending = (
            await current_read_interrupts(db, conversation, context, current)
            if last and not page.has_more and current.run and current.run.status == "interrupted"
            else CurrentReadInterrupts()
        )
        try:
            batch = projection.fold(current, page, pending)
        except ConflictError as exc:
            if exc.code != "AG_UI_SNAPSHOT_REQUIRED":
                raise
            # Missing/compacted generations recover from the selected branch checkpoint.
            return SnapshotRunReplay(visibility=current.visibility, scope=current.scope)
        for frame in batch.frames:
            fold.apply(frame.event, source_ordinal=frame.ordinal)
            result.cursor = frame.id
        for entry in page.entries:
            result.legacy_users.extend(entry.legacy_users)
        owners.observe(page)
        if last:
            break
    result.messages = fold.messages()
    result.message_links = fold.links_by_id()
    result.root_issuances = projection.root_issuances
    result.subagents = fold.subagents()
    result.todos_by_owner = fold.todos_by_owner()
    result.complete = True
    owners.bind(result, str(conversation.id), projection.child_task_handles)
    return result
