"""Reconstruct an owned conversation from committed messages, journal frames and pending slots."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from ag_ui.core import Interrupt, RunErrorEvent
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_v1_outcomes import PendingInterrupts, run_terminal_event
from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.agent_runtime.message_utils import parse_msg_id
from app.agent_runtime.protocol_egress import project_and_redact_protocol_data
from app.agent_runtime.protocol_events import ProtocolInterrupt
from app.authz.conversation_scope import require_private_conversation
from app.authz.org_context import OrgContext
from app.error_codes import resume_not_found
from app.models.conversation import Conversation
from app.models.conversation_run import RUN_ACTIVE_STATUSES, ConversationRun
from app.schemas.ag_ui_snapshot import AgUiSnapshot, AgUiSnapshotMessageLink, SnapshotTodo
from app.services.ag_ui_read_cursor import CursorScope
from app.services.ag_ui_read_pending import CurrentReadInterrupts, read_proven_interrupts
from app.services.ag_ui_read_target import resolve_read_target, snapshot_required
from app.services.ag_ui_snapshot_assembly import AssembledSnapshot, SnapshotAssembly
from app.services.ag_ui_snapshot_history import (
    SnapshotHistory,
    SnapshotMessageRef,
    load_snapshot_history,
)
from app.services.ag_ui_snapshot_journal import SnapshotRunReplay, replay_snapshot_run
from app.services.ag_ui_snapshot_legacy import read_unlinked_legacy_transcript
from app.services.ag_ui_snapshot_legacy_assembly import insert_legacy_transcript
from app.services.ag_ui_snapshot_messages import public_checkpoint_message
from app.services.ag_ui_snapshot_runs import SnapshotRuns, snapshot_runs
from app.services.chat.secrets import collect_conversation_secret_values
from app.services.partial_model_output import (
    PARTIAL_OUTPUT_KEY,
    PARTIAL_STREAM_ERROR,
    read_partial_output,
)


@dataclass(frozen=True, slots=True)
class _RunEvidence:
    """Keep authorization, progress and ownership after releasing a run's journal transcript."""

    scope: CursorScope | None
    visibility: ChildVisibility
    cursor: str | None
    complete: bool
    child_namespaces: dict[tuple[str, ...], str]
    pending_namespaces: dict[str, tuple[str, ...]]
    unresolved_pending_ids: set[str]
    has_root_todos: bool

    @classmethod
    def from_replay(cls, replay: SnapshotRunReplay) -> _RunEvidence:
        return cls(
            replay.scope,
            replay.visibility,
            replay.cursor,
            replay.complete,
            replay.child_namespaces,
            replay.pending_namespaces,
            replay.unresolved_pending_ids,
            None in replay.todos_by_owner,
        )


def _message_link(
    conversation: Conversation,
    ref: SnapshotMessageRef,
    message: dict[str, Any],
    runs: SnapshotRuns,
    parent: str | None,
) -> AgUiSnapshotMessageLink:
    saved = ref.message
    raw_id = saved.get("id") if isinstance(saved, dict) else saved.id
    ui_id = str(
        parse_msg_id(
            raw_id if isinstance(raw_id, str) else None, conversation.id, ref.original_index
        )
    )
    owner = runs.message_runs.get(message["id"])
    timestamp = (conversation.message_timestamps or {}).get(ui_id)
    created = None
    if isinstance(timestamp, str):
        with suppress(ValueError):
            created = datetime.fromisoformat(timestamp)
    return AgUiSnapshotMessageLink(
        message_id=message["id"],
        ui_message_id=ui_id,
        parent_message_id=parent,
        physical_run_id=str(owner) if owner else None,
        run_id=runs.public_ids.get(owner) if owner else None,
        checkpoint_id=ref.checkpoint_id,
        parent_checkpoint_id=ref.parent_checkpoint_id,
        sibling_checkpoint_ids=[] if runs.independent else list(ref.sibling_checkpoint_ids),
        created_at=created,
    )


async def _current_interrupts(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    history: SnapshotHistory,
    run: ConversationRun,
    replay: _RunEvidence,
    merged: AssembledSnapshot,
    public_run_id: str,
    secret_values: Collection[str],
) -> tuple[list[Interrupt], CurrentReadInterrupts | None]:
    if run.status != "interrupted" or history.source != "checkpoint":
        return [], None
    checkpoint = await read_proven_interrupts(
        db, conversation, context, run, checkpoint_id=history.checkpoint_id
    )
    if not checkpoint.interrupts or checkpoint.checkpoint_id != history.checkpoint_id:
        return [], checkpoint
    owners = dict(replay.child_namespaces)
    if checkpoint.legacy_bindings:
        from app.services.ag_ui_snapshot_legacy_children import restore_legacy_children

        owners.update(
            restore_legacy_children(
                str(conversation.id),
                history,
                merged,
                checkpoint.legacy_bindings,
                public_run_id=public_run_id,
                physical_run_id=run.id,
                secret_values=secret_values,
            )
        )
    if replay.visibility.bindings:
        from app.services.ag_ui_snapshot_managed_children import restore_managed_children

        owners.update(
            await restore_managed_children(
                db,
                conversation=conversation,
                context=context,
                run=run,
                public_run_id=public_run_id,
                history=history,
                merged=merged,
                pending=checkpoint,
                visibility=replay.visibility,
                secret_values=secret_values,
            )
        )
    items: list[ProtocolInterrupt] = []
    for item in checkpoint.interrupts:
        ident = item["id"]
        if ident in replay.unresolved_pending_ids and ident not in checkpoint.legacy_bindings:
            continue
        ns = tuple(item["ns"]) or replay.pending_namespaces.get(ident, ())
        items.append(
            ProtocolInterrupt(
                id=ident,
                ns=list(ns),
                value=project_and_redact_protocol_data(
                    "values", item["value"], secret_values=secret_values
                ),
            )
        )
    current_children = {
        row["subagentRunId"]: row
        for row in merged.subagents
        if row.get("physicalRunId") == str(run.id)
    }
    actionable = checkpoint.actionable and len(items) == len(checkpoint.interrupts)
    for item in items:
        if not item["ns"]:
            continue
        child_id = owners.get(tuple(item["ns"]))
        child = current_children.get(child_id) if child_id else None
        if not child or not (child.get("childTaskId") or item["id"] in checkpoint.legacy_child_ids):
            actionable = False
    pending = PendingInterrupts()
    pending.replace_current(
        items,
        resolve_owner=owners.get,
        expires_at=checkpoint.expires_at,
        legacy_child_ids=checkpoint.legacy_child_ids,
        passive=not actionable,
    )
    return pending.public(), checkpoint


def _todos(rows: Sequence[Mapping[str, Any]]) -> list[SnapshotTodo]:
    return [
        SnapshotTodo.model_validate({**row, "id": row.get("id") or f"todo-{i}"})
        for i, row in enumerate(rows, 1)
    ]


async def load_ag_ui_snapshot(
    db: AsyncSession,
    conversation: Conversation,
    context: OrgContext,
    *,
    run_id: UUID | None = None,
    public_run_id: str | None = None,
    projection_version: str = "1",
    cursor: str | None = None,
) -> AgUiSnapshot:
    """Return committed public state at the selected conversation head."""
    if projection_version != "1":
        raise snapshot_required()
    with db.no_autoflush:
        conversation = await require_private_conversation(db, conversation.id, context)
        target = (
            await resolve_read_target(db, conversation, context, public_run_id)
            if public_run_id
            else None
        )
        if target and run_id is not None and (target.run is None or target.run.id != run_id):
            raise resume_not_found()
        requested = target.run.id if target and target.run else run_id
        selection = conversation.active_branch_checkpoint_id
        secrets = await collect_conversation_secret_values(db, conversation, context)
        history = await load_snapshot_history(db, conversation, context, secret_values=secrets)
        entries = [
            (ref, message)
            for ref in history.messages
            if (message := public_checkpoint_message(ref, conversation.id, secret_values=secrets))
            is not None
        ]
        runs = await snapshot_runs(
            db,
            conversation,
            context,
            history,
            [message["id"] for _, message in entries],
            run_id=requested,
        )
        linked = []
        idless_checkpoint_ids: set[str] = set()
        parent = None
        for ref, message in entries:
            linked.append((message, _message_link(conversation, ref, message, runs, parent)))
            saved = ref.message
            raw_id = saved.get("id") if isinstance(saved, dict) else saved.id
            if not raw_id and ref.checkpoint_id in history.checkpoint_run_ids:
                idless_checkpoint_ids.add(message["id"])
            if message["role"] in ("user", "assistant"):
                parent = message["id"]
        assembly = SnapshotAssembly(
            linked,
            include_legacy_users=history.source == "legacy",
            require_run_link=runs.independent,
            idless_checkpoint_ids=idless_checkpoint_ids,
            thread_id=str(conversation.id),
        )
        accepted: dict[UUID, ConversationRun] = {}
        evidence: dict[UUID, _RunEvidence] = {}
        for run in runs.candidates:
            replay = await replay_snapshot_run(
                db,
                conversation=conversation,
                context=context,
                run=run,
                public_id=runs.public_ids[run.id],
                parent_public_id=None,
                secret_values=secrets,
            )
            anchored = run.id in runs.branch_verified
            if not anchored and not assembly.has_root_match(
                replay, run.id, public_run_id=runs.public_ids[run.id]
            ):
                continue
            assembly.add_run(
                run.id, runs.public_ids[run.id], replay, restrict_to_checkpoint=not anchored
            )
            accepted[run.id] = run
            evidence[run.id] = _RunEvidence.from_replay(replay)
        full_runs = {key: run for key, run in accepted.items() if key in runs.branch_verified}
        if requested is not None and requested not in full_runs:
            raise resume_not_found()
        merged = assembly.finish()
        if history.source == "legacy" and not runs.independent:
            old = await read_unlinked_legacy_transcript(
                db, conversation, context, source=history.source, secret_values=secrets
            )
            merged = insert_legacy_transcript(merged, old, accepted)
        latest = next(reversed(full_runs.values()), None)
        chosen = full_runs.get(requested) if requested is not None else latest
        queued = target is not None and target.run is None
        active = next(
            (
                r
                for r in reversed(full_runs.values())
                if r.is_active and r.status in RUN_ACTIVE_STATUSES
            ),
            None,
        )
        if queued:
            active = None
        observed_status = {key: (r.status, r.is_active) for key, r in accepted.items()}
        pending, pending_proof = (
            await _current_interrupts(
                db,
                conversation,
                context,
                history,
                latest,
                evidence[latest.id],
                merged,
                runs.public_ids[latest.id],
                secrets,
            )
            if latest
            else ([], None)
        )
        failed = chosen if chosen and chosen.status in {"failed", "stale"} and not queued else None
        error_code = partial = None
        if failed:
            terminal = run_terminal_event(
                str(conversation.id),
                runs.public_ids[failed.id],
                failed.status,
                PendingInterrupts(),
                error_code=failed.error_code,
            )
            if isinstance(terminal, RunErrorEvent):
                error_code = terminal.code
            if error_code == PARTIAL_STREAM_ERROR:
                partial = read_partial_output(
                    (failed.metadata_json or {}).get(PARTIAL_OUTPUT_KEY), secrets
                )
        # Public cursors identify frames actually represented on the chosen branch. An
        # incoming cursor may refer to a retired chunk generation or an old selection.
        watermark = None
        if chosen and not queued and chosen.id not in merged.unreliable_run_ids:
            recovered = evidence[chosen.id]
            if recovered.complete:
                watermark = recovered.cursor
        root_todos = merged.todos_by_owner.get(None, [])
        known_checkpoints = {
            key for key, owner in history.checkpoint_run_ids.items() if owner in accepted
        }
        leaf_allowed = not runs.independent or history.checkpoint_id in known_checkpoints
        if (
            history.source != "legacy"
            and leaf_allowed
            and not (active and evidence[active.id].has_root_todos)
        ):
            root_todos = list(history.todos)
        if runs.independent:
            for link in merged.message_links:
                if link.checkpoint_id not in known_checkpoints:
                    link.checkpoint_id = None
                if link.parent_checkpoint_id not in known_checkpoints:
                    link.parent_checkpoint_id = None
        children = []
        for row in merged.subagents:
            row = dict(row)
            child_id = row["subagentRunId"]
            # A terminal journal entry can predate a resume, expiry or delegation change.
            row["interruptIds"] = []
            row.pop("legacyInterruptAccess", None)
            row["todos"] = _todos(merged.todos_by_owner.get(child_id, []))
            if latest and row.get("physicalRunId") == str(latest.id):
                slots = [item for item in pending if item.subagent_run_id == child_id]
                if slots:
                    row["status"] = "interrupted"
                    row["interruptIds"] = [item.id for item in slots]
                    row["legacyInterruptAccess"] = any(
                        (item.metadata or {}).get("moldy", {}).get("legacyInterruptAccess") is True
                        for item in slots
                    )
            children.append(row)
        snapshot = AgUiSnapshot.model_validate(
            {
                "threadId": str(conversation.id),
                "checkpointId": history.checkpoint_id if leaf_allowed else None,
                "activeTipMessageId": merged.active_tip_message_id,
                "runId": (
                    public_run_id if queued else runs.public_ids[chosen.id] if chosen else None
                ),
                "runStatus": target.input_status
                if queued and target
                else chosen.status
                if chosen
                else None,
                "selectedPhysicalRunId": str(chosen.id) if chosen and not queued else None,
                "latestPhysicalRunId": str(latest.id) if latest else None,
                "activeRunId": runs.public_ids[active.id] if active else None,
                "physicalRunId": str(active.id) if active else None,
                "interruptRunId": runs.public_ids[latest.id] if pending and latest else None,
                "physicalInterruptRunId": str(latest.id) if pending and latest else None,
                "inputId": str(target.input_id) if target and target.input_id else None,
                "inputStatus": target.input_status if target else None,
                "inputRevision": target.revision if target else None,
                "queueStatus": target.input_status if target else None,
                "position": target.position if target else None,
                "failedPhysicalRunId": str(failed.id) if failed else None,
                "errorCode": error_code,
                "partialOutput": partial,
                "messages": merged.messages,
                "messageLinks": merged.message_links,
                "interrupts": pending,
                "subagents": children,
                "todos": _todos(root_todos),
                "cursor": watermark,
            }
        )
        current = await require_private_conversation(db, conversation.id, context)
        if current.active_branch_checkpoint_id != selection:
            raise snapshot_required()
        for ident, recovered in evidence.items():
            final_target = await resolve_read_target(db, current, context, runs.public_ids[ident])
            final_run = final_target.run
            if (
                final_target.scope != recovered.scope
                or final_run is None
                or (final_run.status, final_run.is_active) != observed_status[ident]
            ):
                raise snapshot_required()
        if queued and target and public_run_id:
            latest_target = await resolve_read_target(db, current, context, public_run_id)
            if latest_target.run is not None or (
                latest_target.input_status,
                latest_target.revision,
                latest_target.position,
            ) != (target.input_status, target.revision, target.position):
                raise snapshot_required()
        if pending_proof is not None and latest:
            fresh = await read_proven_interrupts(
                db, current, context, latest, checkpoint_id=history.checkpoint_id
            )
            if fresh != pending_proof:
                raise snapshot_required()
        return snapshot
