"""Restore a widget from the selected root checkpoint and the current private widget grant."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from ag_ui.core import ActivitySnapshotEvent

from app.agent_runtime.ag_ui_v1_activities import PublicActivities
from app.agent_runtime.ag_ui_v1_issuance import public_tool_call_id, public_tool_result_id
from app.agent_runtime.message_utils import parse_msg_id
from app.agent_runtime.protocol_events import stored_protocol_event
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.services.ag_ui_snapshot_assembly import AssembledSnapshot
from app.services.ag_ui_snapshot_history import SnapshotHistory, SnapshotMessageRef
from app.services.ag_ui_snapshot_messages import _saved_field

type LinkedRow = tuple[dict[str, Any], AgUiSnapshotMessageLink]
type Grants = Mapping[UUID, frozenset[tuple[str, str]]]
type BindingKey = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class _Occurrence:
    ref: SnapshotMessageRef
    position: int
    run: UUID | None
    visible: LinkedRow | None
    name: str | None


def _visible_index(merged: AssembledSnapshot) -> dict[tuple[str, str, str], LinkedRow | None]:
    index: dict[tuple[str, str, str], LinkedRow | None] = {}
    for row, link in zip(merged.messages, merged.message_links, strict=True):
        if (
            link.message_id == row.get("id")
            and not row.get("subagentRunId")
            and not link.subagent_run_id
            and link.checkpoint_id
            and link.ui_message_id
            and row.get("role") in ("assistant", "tool")
        ):
            key = link.checkpoint_id, link.ui_message_id, row["role"]
            index[key] = None if key in index else (row, link)
    return index


def _checkpoint_cards(
    thread: UUID,
    history: SnapshotHistory,
    merged: AssembledSnapshot,
    public_ids: Mapping[UUID, str],
    grants: Grants,
) -> list[tuple[str, LinkedRow]]:
    if (
        history.source != "checkpoint"
        or not history.lineage_checkpoint_ids
        or history.lineage_checkpoint_ids[-1] != history.checkpoint_id
        or not grants
    ):
        return []
    lineage = set(history.lineage_checkpoint_ids)
    selected = {str(uid): uid for uid in public_ids if uid not in history.other_branch_run_ids}
    wanted = {raw for uid, grant in grants.items() if str(uid) in selected for raw, _ in grant}
    if not wanted:
        return []
    visible = _visible_index(merged)
    issuers: dict[str, list[_Occurrence]] = defaultdict(list)
    results: dict[str, list[_Occurrence]] = defaultdict(list)
    for position, ref in enumerate(history.messages):
        native = ref.message
        role = _saved_field(native, "type") or _saved_field(native, "role")
        if role not in ("ai", "assistant", "tool"):
            continue
        src = _saved_field(native, "id")
        ui_id = str(parse_msg_id(src if isinstance(src, str) else None, thread, ref.original_index))
        cp = ref.checkpoint_id
        view = visible.get((cp, ui_id, "tool" if role == "tool" else "assistant")) if cp else None
        physical = history.checkpoint_run_ids.get(cp) if cp else None
        if view:
            link = view[1]
            linked_run = selected.get(link.physical_run_id or "")
            if (
                not linked_run
                or link.run_id != public_ids[linked_run]
                or (physical is not None and physical != linked_run)
                or cp not in lineage
            ):
                view = None
            else:
                physical = linked_run
        if role == "tool":
            raw = _saved_field(native, "tool_call_id")
            if isinstance(raw, str) and raw in wanted:
                results[raw].append(
                    _Occurrence(ref, position, physical, view, _saved_field(native, "name"))
                )
        else:
            calls = _saved_field(native, "tool_calls")
            for call in calls if isinstance(calls, list) else ():
                if (
                    isinstance(call, Mapping)
                    and isinstance(raw := call.get("id"), str)
                    and raw in wanted
                ):
                    issuers[raw].append(
                        _Occurrence(ref, position, physical, view, call.get("name"))
                    )

    cards = []
    for run, grant in grants.items():
        if str(run) not in selected:
            continue
        for raw, binding in grant:
            # The DB binding addresses a raw id inside one physical run. A second
            # native issuer/result of unknown origin makes that association ambiguous.
            start = [item for item in issuers[raw] if item.run in (None, run)]
            end = [item for item in results[raw] if item.run in (None, run)]
            if len(start) != 1 or len(end) != 1:
                continue
            issuer, result = start[0], end[0]
            if (
                not issuer.visible
                or not result.visible
                or issuer.position >= result.position
                or not issuer.name
                or result.name not in (None, issuer.name)
                or _saved_field(result.ref.message, "status") not in (None, "success")
            ):
                continue
            row, link = issuer.visible
            result_row, result_link = result.visible
            call = public_tool_call_id(public_ids[run], None, link.message_id, raw)
            functions = [
                obj.get("function")
                for obj in row.get("toolCalls", [])
                if isinstance(obj, Mapping) and obj.get("id") == call
            ]
            if (
                len(functions) != 1
                or not isinstance(functions[0], Mapping)
                or functions[0].get("name") != issuer.name
                or result_row.get("toolCallId") != call
                or result_row.get("id") != public_tool_result_id(call)
                or result_link.parent_tool_call_id not in (None, call)
            ):
                continue
            artifact = _saved_field(result.ref.message, "artifact")
            pointer = artifact.get("mcp_app") if isinstance(artifact, Mapping) else None
            if (
                not isinstance(pointer, Mapping)
                or type(pointer.get("version")) is not int
                or pointer["version"] != 1
                or pointer.get("run_id") != str(run)
                or pointer.get("binding_id") != binding
            ):
                continue
            # Use the live projector for both shape and identity. Only inert pointer
            # fields enter it; saved tool contents and private widget data stay private.
            event = stored_protocol_event(
                run_id=str(run),
                thread_id=str(thread),
                seq=0,
                method="tools",
                data={
                    "event": "tool-finished",
                    "tool_call_id": raw,
                    "artifact": {
                        "mcp_app": {
                            "version": 1,
                            "run_id": str(run),
                            "binding_id": binding,
                        }
                    },
                },
            )
            frames = PublicActivities(readable_widget_bindings=grant).project(
                event,
                run_id=public_ids[run],
                public_message_id=link.message_id,
                public_tool_call_id=call,
            )
            if len(frames) != 1 or not isinstance(frame := frames[0], ActivitySnapshotEvent):
                continue
            card = {
                "id": frame.message_id,
                "role": "activity",
                "activityType": frame.activity_type,
                "content": frame.content,
            }
            card_link = AgUiSnapshotMessageLink(
                message_id=frame.message_id,
                physical_run_id=str(run),
                run_id=public_ids[run],
                parent_message_id=link.message_id,
                parent_tool_call_id=call,
            )
            cards.append((result_link.message_id, (card, card_link)))
    return cards


def _authorized_key(
    row: dict[str, Any], link: AgUiSnapshotMessageLink, grants: set[BindingKey]
) -> BindingKey | None:
    data = row.get("content")
    if not isinstance(data, dict) or data.keys() - {
        "run_id",
        "tool_call_id",
        "binding_id",
        "public_tool_call_id",
        "public_message_id",
    }:
        return None
    run, raw, binding = data.get("run_id"), data.get("tool_call_id"), data.get("binding_id")
    if not isinstance(run, str) or not isinstance(raw, str) or not isinstance(binding, str):
        return None
    key = run, raw, binding
    if (
        key in grants
        and run == link.physical_run_id
        and row.get("id") == link.message_id
        and link.subagent_run_id == row.get("subagentRunId")
        and link.parent_tool_call_id
        and link.parent_tool_call_id == data.get("public_tool_call_id")
        and data.get("public_message_id") in (None, link.parent_message_id)
    ):
        return key
    return None


def restore_snapshot_widgets(
    *,
    conversation_id: UUID,
    history: SnapshotHistory,
    merged: AssembledSnapshot,
    public_run_ids: Mapping[UUID, str],
    widget_bindings: Grants,
) -> None:
    """Join authorized checkpoint-only root widgets and retain authorized journal activities."""
    additions = _checkpoint_cards(conversation_id, history, merged, public_run_ids, widget_bindings)
    if not additions and not any(
        row.get("activityType") == "moldy.mcp_app" for row in merged.messages
    ):
        return
    runs = {str(uid): uid for uid in set(public_run_ids) | set(widget_bindings)}
    grants = {
        (str(uid), raw, binding)
        for uid, values in widget_bindings.items()
        for raw, binding in values
    }
    retained: list[LinkedRow] = []
    known: dict[BindingKey, list[LinkedRow]] = defaultdict(list)
    for row, link in zip(merged.messages, merged.message_links, strict=True):
        if row.get("role") != "activity" or row.get("activityType") != "moldy.mcp_app":
            retained.append((row, link))
            continue
        if key := _authorized_key(row, link, grants):
            known[key].append((row, link))
            retained.append((row, link))
        elif physical := runs.get(link.physical_run_id or ""):
            merged.unreliable_run_ids.add(physical)
    # Two public issuers cannot claim the same physical binding. Never choose one
    # by order. Identical retained/reconstructed cards share the live activity id.
    after: dict[str, list[LinkedRow]] = defaultdict(list)
    retained_ids = {row["id"] for row, _ in retained}
    conflicts: set[str] = set()
    for result, (card, link) in additions:
        data = card["content"]
        key = data["run_id"], data["tool_call_id"], data["binding_id"]
        if group := known.get(key):
            if any(
                other["id"] != card["id"]
                or old.parent_tool_call_id != link.parent_tool_call_id
                or old.parent_message_id != link.parent_message_id
                for other, old in group
            ):
                conflicts.update(other["id"] for other, _ in group)
                merged.unreliable_run_ids.add(runs[key[0]])
        elif card["id"] in retained_ids:
            merged.unreliable_run_ids.add(runs[key[0]])
        else:
            after[result].append((card, link))
            retained_ids.add(card["id"])
    for key, group in known.items():
        if len(group) > 1:
            conflicts.update(row["id"] for row, _ in group)
            merged.unreliable_run_ids.add(runs[key[0]])
    final: list[LinkedRow] = []
    for row, link in retained:
        if row.get("activityType") == "moldy.mcp_app" and row["id"] in conflicts:
            continue
        final.append((row, link))
        final.extend(after.get(row["id"], ()))
    merged.messages[:] = [row for row, _ in final]
    merged.message_links[:] = [link for _, link in final]
