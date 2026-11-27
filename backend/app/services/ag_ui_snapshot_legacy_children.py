"""Restore the issuer of a current, authorized pre-enrollment delegation."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING
from uuid import UUID

from app.agent_runtime.ag_ui_v1_children import public_subagent_id
from app.agent_runtime.ag_ui_v1_issuance import public_tool_call_id
from app.agent_runtime.lease_checkpoint_namespace import NS_SEP
from app.agent_runtime.message_utils import parse_msg_id
from app.marketplace.redaction import replace_secret_values
from app.services.ag_ui_snapshot_messages import _saved_field
from app.tools.keys import DELEGATION_TOOL

if TYPE_CHECKING:
    from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
    from app.services.ag_ui_legacy_child_binding import LegacyChildBinding
    from app.services.ag_ui_snapshot_assembly import AssembledSnapshot, Row
    from app.services.ag_ui_snapshot_history import SnapshotHistory, SnapshotMessageRef


def _issuing_message(history: SnapshotHistory) -> SnapshotMessageRef | None:
    # The native ToolNode Send inspector proves calls on the latest root AIMessage.
    # Looking farther back could reassociate a provider-local ID from an older turn.
    for ref in reversed(history.messages):
        role = _saved_field(ref.message, "type") or _saved_field(ref.message, "role")
        if role in ("ai", "assistant"):
            return ref
    return None


def _issuer_view(
    thread_id: str, history: SnapshotHistory, merged: AssembledSnapshot, ref: SnapshotMessageRef
) -> tuple[Row, AgUiSnapshotMessageLink] | None:
    source_id = _saved_field(ref.message, "id")
    if not isinstance(source_id, str) or not source_id:
        return None
    if sum(_saved_field(item.message, "id") == source_id for item in history.messages) != 1:
        return None
    ui_id = str(parse_msg_id(source_id, UUID(thread_id), ref.original_index))
    matched = [
        (row, link)
        for row, link in zip(merged.messages, merged.message_links, strict=True)
        if row.get("role") == "assistant"
        and not row.get("subagentRunId")
        and not link.subagent_run_id
        and link.checkpoint_id == ref.checkpoint_id
        and link.message_id == row.get("id")
        and (link.message_id == source_id or link.ui_message_id == ui_id)
    ]
    if len(matched) != 1:
        return None
    return matched[0]


def restore_legacy_children(
    thread_id: str,
    history: SnapshotHistory,
    merged: AssembledSnapshot,
    bindings: Mapping[str, LegacyChildBinding],
    *,
    public_run_id: str,
    physical_run_id: UUID,
    secret_values: Collection[str],
) -> dict[tuple[str, ...], str]:
    """Use only bindings from successful current read_pending_child_authority.

    A proof of the present pending slot can belong to a prior physical run in a
    resume chain. The public tool ID comes from its issuer. The child row belongs
    to the current pending run. This routine never grants a durable child handle.
    """
    if not bindings or history.source != "checkpoint" or not history.checkpoint_id:
        return {}
    ref = _issuing_message(history)
    if (
        ref is None
        or ref.checkpoint_id is None
        or ref.checkpoint_id not in history.lineage_checkpoint_ids
        or history.checkpoint_id not in history.lineage_checkpoint_ids
    ):
        return {}
    view = _issuer_view(thread_id, history, merged, ref)
    if view is None:
        return {}
    row, link = view
    if not link.physical_run_id or not link.run_id:
        return {}
    native_calls = _saved_field(ref.message, "tool_calls")
    if not isinstance(native_calls, list) or not isinstance(
        public_calls := row.get("toolCalls"), list
    ):
        return {}
    roles_by_id: dict[str, list[object]] = defaultdict(list)
    for call in native_calls:
        if isinstance(call, Mapping) and isinstance(cid := call.get("id"), str):
            args = call.get("args")
            role = (
                args.get("subagent_type")
                if isinstance(args, Mapping) and call.get("name") == DELEGATION_TOOL
                else None
            )
            roles_by_id[cid].append(role)
    names_by_id: dict[str, list[object]] = defaultdict(list)
    for call in public_calls:
        if isinstance(call, Mapping) and isinstance(cid := call.get("id"), str):
            func = call.get("function")
            names_by_id[cid].append(func.get("name") if isinstance(func, Mapping) else None)

    # Several current interrupts can share one task. A raw call assigned to two
    # namespaces, or a single namespace with incompatible proof, cannot be joined.
    unique = tuple(dict.fromkeys(bindings.values()))
    namespaces = Counter(binding.checkpoint_ns for binding in unique)
    raw_ids = Counter(binding.tool_call_id for binding in unique)
    existing: dict[str, list[Row]] = defaultdict(list)
    for child in merged.subagents:
        existing[child["subagentRunId"]].append(child)
    restored = {}
    for binding in unique:
        namespace = tuple(binding.checkpoint_ns.split(NS_SEP))
        raw = binding.tool_call_id
        producer = binding.producer_run_id or physical_run_id
        if (
            len(namespace) != 1
            or not namespace[0].startswith("tools:")
            or namespace[0] == "tools:"
            or namespaces[binding.checkpoint_ns] != 1
            or raw_ids[raw] != 1
            or roles_by_id.get(raw) != [binding.runtime_name]
            or str(producer) != link.physical_run_id
            or (producer == physical_run_id and link.run_id != public_run_id)
            or history.checkpoint_run_ids.get(ref.checkpoint_id, producer) != producer
            or producer in history.other_branch_run_ids
        ):
            continue
        issuer_id = public_tool_call_id(link.run_id, None, link.message_id, raw)
        if names_by_id.get(issuer_id) != [DELEGATION_TOOL]:
            continue
        cid = public_subagent_id(thread_id, namespace)
        known = existing.get(cid, [])
        if len(known) > 1:
            continue
        if known:
            child = known[0]
            if (
                child.get("childTaskId")
                or child.get("parentSubagentRunId")
                or child.get("parentToolCallId") not in (None, issuer_id)
                or child.get("parentMessageId") not in (None, link.message_id)
                or (child.get("runId"), child.get("physicalRunId"))
                not in ((public_run_id, str(physical_run_id)), (link.run_id, str(producer)))
            ):
                continue
            for key in ("result", "error", "interruptIds", "legacyInterruptAccess"):
                child.pop(key, None)
        else:
            child = {"subagentRunId": cid}
            merged.subagents.append(child)
        child.update(
            name=replace_secret_values(
                binding.runtime_name, secret_values, placeholder="<redacted>"
            ),
            parentMessageId=link.message_id,
            parentToolCallId=issuer_id,
            runId=public_run_id,
            physicalRunId=str(physical_run_id),
            status="interrupted",
        )
        restored[namespace] = cid
    return restored
