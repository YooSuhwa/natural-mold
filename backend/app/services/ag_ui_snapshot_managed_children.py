"""Restore a waiting managed child from its durable invocation and selected root issuer."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping
from datetime import datetime
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.ag_ui_v1_children import PublicChildren, public_subagent_id
from app.agent_runtime.ag_ui_v1_issuance import public_tool_call_id
from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.agent_runtime.lease_checkpoint_namespace import NS_SEP
from app.approvals import service as approvals
from app.authz.org_context import OrgContext
from app.marketplace.redaction import replace_secret_values
from app.models.conversation import INDEPENDENT_JOB_SOURCE, Conversation
from app.models.conversation_child_task import ConversationChildTask
from app.models.conversation_run import ConversationRun
from app.services.ag_ui_pending_checkpoint import _run_lineage
from app.services.ag_ui_read_pending import CurrentReadInterrupts
from app.services.ag_ui_snapshot_assembly import AssembledSnapshot, Row
from app.services.ag_ui_snapshot_history import SnapshotHistory
from app.services.ag_ui_snapshot_legacy_children import _issuer_view, _issuing_message
from app.services.ag_ui_snapshot_messages import _saved_field
from app.services.child_task_read_bindings import readable_child_task_visibility
from app.tools.keys import DELEGATION_TOOL


class _WaitingTask(NamedTuple):
    id: UUID
    call: str
    namespace: str
    role: str


def _has_expired_slot(pending: CurrentReadInterrupts) -> bool:
    # Deadlines are server timestamps from the current authorized approval rows.
    now = approvals.now()
    for slot in pending.interrupts:
        expiry = pending.expires_at.get(slot["id"])
        if expiry is None:
            continue
        try:
            deadline = datetime.fromisoformat(expiry)
        except ValueError:
            continue
        if deadline.tzinfo is not None and deadline <= now:
            return True
    return False


async def restore_managed_children(
    db: AsyncSession,
    *,
    conversation: Conversation,
    context: OrgContext,
    run: ConversationRun,
    public_run_id: str,
    history: SnapshotHistory,
    merged: AssembledSnapshot,
    pending: CurrentReadInterrupts,
    visibility: ChildVisibility,
    secret_values: Collection[str],
) -> dict[tuple[str, ...], str]:
    """Expose only a currently authorized, native pending task with its actual issuing call."""
    if (
        pending.child_authority_denied
        or (not pending.actionable and not _has_expired_slot(pending))
        or not visibility.bindings
        or visibility.allow_legacy_details
        or history.source != "checkpoint"
        or not history.checkpoint_id
        or pending.checkpoint_id != history.checkpoint_id
        or run.status != "interrupted"
        or run.id in history.other_branch_run_ids
        or run.agent_id is None
        or run.conversation_id != conversation.id
        or conversation.source == INDEPENDENT_JOB_SOURCE
        or conversation.agent_id != run.agent_id
    ):
        return {}
    ref = _issuing_message(history)
    if ref is None or ref.checkpoint_id not in history.lineage_checkpoint_ids:
        return {}
    thread_id = str(conversation.id)
    view = _issuer_view(thread_id, history, merged, ref)
    if view is None or ref.checkpoint_id is None:
        return {}
    issuer, link = view
    producer = history.checkpoint_run_ids.get(ref.checkpoint_id)
    if (
        not producer
        or producer in history.other_branch_run_ids
        or link.physical_run_id != str(producer)
        or not link.run_id
        or (producer == run.id and link.run_id != public_run_id)
    ):
        return {}
    calls = _saved_field(ref.message, "tool_calls")
    exposed = issuer.get("toolCalls")
    if not isinstance(calls, list) or not isinstance(exposed, list):
        return {}
    # Keep all occurrences: even an otherwise identical duplicate does not prove the issuer.
    native: dict[str, list[tuple[object, object]]] = defaultdict(list)
    public: dict[str, list[object]] = defaultdict(list)
    for call in calls:
        if isinstance(call, Mapping) and isinstance(raw := call.get("id"), str):
            args = call.get("args")
            role = args.get("subagent_type") if isinstance(args, Mapping) else None
            native[raw].append((call.get("name"), role))
    for call in exposed:
        if isinstance(call, Mapping) and isinstance(cid := call.get("id"), str):
            fn = call.get("function")
            public[cid].append(fn.get("name") if isinstance(fn, Mapping) else None)

    slots = {
        tuple(slot["ns"])
        for slot in pending.interrupts
        if slot["ns"] and slot["id"] not in pending.legacy_bindings
    }
    if not slots:
        return {}
    with db.no_autoflush:
        lineage = await _run_lineage(db, run.id, conversation.id, run.agent_id, context)
        if producer not in lineage:
            return {}
        current = await readable_child_task_visibility(db, conversation, context, run.id)
        policy = PublicChildren(thread_id, current)
        bindings = visibility.bindings & current.bindings
        if not bindings:
            return {}
        # The authorization reader pairs call, namespace and pinned participant, rechecking
        # the live grant. Rebind/finish/cancel between reads must not restore the old handle.
        ids = [UUID(task_id) for _, _, task_id in bindings]
        task = ConversationChildTask
        rows = await db.execute(
            select(task.id, task.tool_call_id, task.checkpoint_ns, task.role).where(
                task.id.in_(ids),
                task.run_id == producer,
                task.current_run_id.in_(lineage),
                task.status == "awaiting_approval",
            )
        )
        waiting = [_WaitingTask(*row) for row in rows]

    existing: dict[str, list[Row]] = defaultdict(list)
    for child in merged.subagents:
        existing[child["subagentRunId"]].append(child)
    # A cancelled or removed descendant must not transfer its still-visible slot to an ancestor.
    known_namespaces = visibility.namespaces | current.namespaces
    restored: dict[tuple[str, ...], str] = {}
    for child in waiting:
        ns = tuple(child.namespace.split(NS_SEP))
        if (
            (child.call, ns, str(child.id)) not in bindings
            or not policy.allow_scoped_tool(DELEGATION_TOOL, child.call, None)
            or native.get(child.call) != [(DELEGATION_TOOL, child.role)]
        ):
            continue
        matched = {
            slot
            for slot in slots
            if slot[: len(ns)] == ns
            and not any(
                len(other) > len(ns) and slot[: len(other)] == other for other in known_namespaces
            )
        }
        opaque = public_tool_call_id(link.run_id, None, link.message_id, child.call)
        cid = public_subagent_id(thread_id, ns)
        if not matched or public.get(opaque) != [DELEGATION_TOOL]:
            continue
        if len(matches := existing.get(cid, [])) > 1:
            continue
        if matches:
            row = matches[0]
            if (
                row.get("childTaskId") not in (None, str(child.id))
                or row.get("parentSubagentRunId")
                or row.get("parentToolCallId") not in (None, opaque)
                or row.get("parentMessageId") not in (None, link.message_id)
                or (row.get("runId"), row.get("physicalRunId"))
                not in ((public_run_id, str(run.id)), (link.run_id, str(producer)))
            ):
                continue
            for key in ("result", "error", "interruptIds", "legacyInterruptAccess"):
                row.pop(key, None)
        else:
            row = {"subagentRunId": cid}
            merged.subagents.append(row)
        row.update(
            name=replace_secret_values(child.role, secret_values, placeholder="<redacted>"),
            parentMessageId=link.message_id,
            parentToolCallId=opaque,
            runId=public_run_id,
            physicalRunId=str(run.id),
            status="interrupted",
        )
        if pending.actionable:
            row["childTaskId"] = str(child.id)
        else:
            row.pop("childTaskId", None)
        restored.update(dict.fromkeys(matched, cid))
    return restored
