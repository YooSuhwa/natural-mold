"""Reconcile the selected checkpoint with safe views of its authorized runs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink

type Row = dict[str, Any]


class _Replay(Protocol):
    @property
    def messages(self) -> Sequence[Mapping[str, Any]]: ...
    @property
    def legacy_users(self) -> Sequence[Mapping[str, Any]]: ...
    @property
    def message_links(self) -> Mapping[str, Mapping[str, Any]]: ...
    @property
    def subagents(self) -> Sequence[Mapping[str, Any]]: ...
    @property
    def todos_by_owner(self) -> Mapping[str | None, Sequence[Mapping[str, Any]]]: ...


@dataclass(slots=True)
class _Entry:
    message: Row
    link: AgUiSnapshotMessageLink


@dataclass(slots=True)
class AssembledSnapshot:
    messages: list[Row]
    message_links: list[AgUiSnapshotMessageLink]
    subagents: list[Row]
    todos_by_owner: dict[str | None, list[Row]]
    active_tip_message_id: str | None
    unreliable_run_ids: set[UUID]


def _call_ids(row: Mapping[str, Any]) -> set[str]:
    calls = row.get("toolCalls")
    if not isinstance(calls, list):
        return set()
    return {
        call["id"] for call in calls if isinstance(call, dict) and isinstance(call.get("id"), str)
    }


def _merge_assistant(
    saved: Mapping[str, Any], row: Mapping[str, Any], *, committed_only: bool, journal_only: bool
) -> Row:
    """Limit content to the common prefix whenever both sources constrain visibility."""
    merged = dict(row)
    saved_text = saved.get("content") or ""
    text = row.get("content") or ""
    # _match already verified that the shorter visible text is the prefix.
    if committed_only and (not journal_only or len(saved_text) < len(text)):
        if "content" in saved:
            merged["content"] = saved["content"]
        else:
            merged.pop("content", None)
    elif not journal_only and len(saved_text) > len(text):
        merged["content"] = saved_text
    # Only the public journal can fill in tool arguments. Retained checkpoints
    # can still provide the visible call skeleton after the journal is compacted.
    journal_calls = {call["id"]: call for call in row.get("toolCalls", [])}
    calls = []
    for saved_call in saved.get("toolCalls", []):
        if call := journal_calls.pop(saved_call["id"], None):
            calls.append(call)
        elif not journal_only:
            calls.append(saved_call)
    if not committed_only:
        calls.extend(journal_calls.values())
    if calls:
        merged["toolCalls"] = calls
    else:
        merged.pop("toolCalls", None)
    return merged


class SnapshotAssembly:
    """Keep checkpoint order and insert each run's extra rows beside its durable messages."""

    def __init__(
        self,
        checkpoint: Sequence[tuple[Row, AgUiSnapshotMessageLink]],
        *,
        include_legacy_users: bool = False,
        require_run_link: bool = False,
    ) -> None:
        self._legacy = include_legacy_users and not checkpoint
        self._require_run = require_run_link
        self._checkpoint = [_Entry(deepcopy(row), link) for row, link in checkpoint]
        self._by_id: dict[str, list[int]] = defaultdict(list)
        self._by_call: dict[tuple[str, str], set[int]] = defaultdict(set)
        self._bound: dict[str, list[int]] = defaultdict(list)
        self._occupied: set[str] = set()
        self._extras: dict[int, list[_Entry]] = defaultdict(list)
        self._aliases: dict[str, str] = {}
        self._runs: set[str] = set()
        self._unreliable: set[UUID] = set()
        self._children: dict[str, Row] = {}
        self._todos: dict[str | None, list[Row]] = {}
        for index, entry in enumerate(self._checkpoint):
            row = entry.message
            mid = row["id"]
            self._by_id[mid].append(index)
            self._occupied.add(mid)
            if entry.link.physical_run_id:
                self._bound[str(entry.link.physical_run_id)].append(index)
            if row["role"] == "assistant":
                for cid in _call_ids(row):
                    self._by_call["assistant", cid].add(index)
            elif row["role"] == "tool":
                self._by_call["tool", row["toolCallId"]].add(index)

    def _match(self, row: Mapping[str, Any], physical_id: str | None) -> int | None:
        if row.get("subagentRunId"):
            return None
        role = row.get("role")
        mid = row.get("id")
        if not isinstance(mid, str) or role not in ("assistant", "tool", "user"):
            return None
        indices = self._by_id.get(mid)
        if indices:
            if len(indices) != 1:
                return None
            index = indices[0]
        else:
            if role == "tool":
                keys = (row.get("toolCallId"),)
            elif role == "assistant":
                keys = _call_ids(row)
            else:
                return None
            candidates: set[int] = set()
            for key in keys:
                if isinstance(key, str):
                    candidates.update(self._by_call.get((role, key), ()))
            if len(candidates) != 1:
                return None
            index = candidates.pop()
        entry = self._checkpoint[index]
        known_owner = entry.link.physical_run_id
        if entry.message["role"] != role or (
            known_owner and physical_id and str(known_owner) != physical_id
        ):
            return None
        if role == "assistant":
            saved_text, text = entry.message.get("content"), row.get("content")
            if (
                isinstance(saved_text, str)
                and isinstance(text, str)
                and not (saved_text.startswith(text) or text.startswith(saved_text))
            ):
                return None
            saved_calls, calls = _call_ids(entry.message), _call_ids(row)
            if saved_calls and calls and saved_calls.isdisjoint(calls):
                return None
        if role == "tool" and row.get("toolCallId") != entry.message.get("toolCallId"):
            return None
        if role == "user" and row.get("content") != entry.message.get("content"):
            return None
        return index

    def has_root_match(self, replay: _Replay, physical_run_id: UUID | None = None) -> bool:
        """An anchor on this branch establishes a prefix, never a whole historic fork."""
        return any(
            row.get("role") in ("assistant", "tool")
            and self._match(row, str(physical_run_id) if physical_run_id else None) is not None
            for row in replay.messages
        )

    def _merge_checkpoint(
        self,
        index: int,
        row: Mapping[str, Any],
        public_id: str,
        physical_id: str,
        restricted: bool,
        meta: Mapping[str, Any],
    ) -> bool:
        entry = self._checkpoint[index]
        saved = entry.message
        old_id, mid = saved["id"], row["id"]
        if mid != old_id:
            if mid in self._occupied:
                return False
            self._occupied.add(mid)
            self._aliases[old_id] = mid
        merged = dict(row)
        checkpoint_link = entry.link
        unbound_job = self._require_run and not checkpoint_link.physical_run_id
        if saved["role"] == "assistant":
            merged = _merge_assistant(
                saved, row, committed_only=restricted, journal_only=unbound_job
            )
            if unbound_job and (
                (saved.get("content") or "") != (row.get("content") or "")
                or _call_ids(saved) != _call_ids(row)
            ):
                # A reused provider ID can prove part of a stream without proving
                # that the checkpoint is an authorized feedback/edit target.
                checkpoint_link = AgUiSnapshotMessageLink(message_id=mid)
        entry.message = merged
        update = {"message_id": mid, "run_id": public_id, "physical_run_id": physical_id}
        for source, field in (
            ("parentMessageId", "parent_message_id"),
            ("parentToolCallId", "parent_tool_call_id"),
        ):
            if isinstance(value := meta.get(source), str):
                update[field] = value
        entry.link = checkpoint_link.model_copy(update=update)
        # A cursor promises that replaying the suffix cannot duplicate or change
        # any portion of this prefix, including a checkpoint that is further ahead.
        return merged == row

    def add_run(
        self,
        physical_run_id: UUID,
        public_run_id: str,
        replay: _Replay,
        *,
        restrict_to_checkpoint: bool = False,
    ) -> None:
        """Call in chronological order after verifying the physical run and branch."""
        physical = str(physical_run_id)
        if physical in self._runs:
            raise ValueError("A snapshot must fold each authorized physical run once")
        self._runs.add(physical)
        rows = [*replay.legacy_users, *replay.messages]
        matches = [self._match(row, physical) for row in rows]
        anchors = [index for index in matches if index is not None]
        bound = self._bound.get(physical, [])
        # Extras before this run's first known message follow the preceding turn.
        # Active partial-only and legacy histories without checkpoints append at the tip.
        slot = anchors[0] if anchors else (bound[-1] + 1 if bound else len(self._checkpoint))
        completed_calls = {
            str(row["toolCallId"])
            for row, index in zip(rows, matches, strict=True)
            if index is not None and row.get("role") == "tool"
        }
        children = self._add_children(
            replay.subagents,
            physical_run_id,
            public_run_id,
            restrict_to_checkpoint,
            completed_calls,
        )
        seen: set[int] = set()
        for row_number, (row, index) in enumerate(zip(rows, matches, strict=True)):
            mid = row.get("id")
            if not isinstance(mid, str) or not mid:
                self._unreliable.add(physical_run_id)
                continue
            meta = replay.message_links.get(mid, {})
            if index is not None:
                if index in seen or not self._merge_checkpoint(
                    index, row, public_run_id, physical, restrict_to_checkpoint, meta
                ):
                    self._unreliable.add(physical_run_id)
                seen.add(index)
                slot = index + 1
                continue
            legacy_user = row_number < len(replay.legacy_users)
            if legacy_user and not self._legacy:
                continue
            child = row.get("subagentRunId")
            proven_detail = (child and child in children) or (
                row.get("role") == "activity" and meta.get("parentToolCallId") in completed_calls
            )
            if (
                (child and child not in children)
                or (restrict_to_checkpoint and not proven_detail)
                or mid in self._occupied
            ):
                self._unreliable.add(physical_run_id)
                continue
            self._occupied.add(mid)
            link = AgUiSnapshotMessageLink(
                message_id=mid,
                run_id=public_run_id,
                physical_run_id=physical,
                subagent_run_id=child,
                parent_message_id=meta.get("parentMessageId"),
                parent_tool_call_id=meta.get("parentToolCallId"),
            )
            self._extras[slot].append(_Entry(dict(row), link))
        if any(
            index not in seen and self._checkpoint[index].message["role"] != "user"
            for index in bound
        ):
            self._unreliable.add(physical_run_id)
        for owner, todos in replay.todos_by_owner.items():
            if (owner is not None or restrict_to_checkpoint) and owner not in children:
                self._unreliable.add(physical_run_id)
                continue
            self._todos[owner] = [dict(todo) for todo in todos]

    def _add_children(
        self,
        records: Sequence[Mapping[str, Any]],
        physical: UUID,
        public_id: str,
        restricted: bool,
        completed_calls: set[str],
    ) -> set[str]:
        accepted: set[str] = set()
        for item in records:
            cid = item.get("subagentRunId")
            parent = item.get("parentSubagentRunId")
            if not isinstance(cid, str) or (
                restricted
                and not (
                    parent in accepted
                    if parent
                    else item.get("parentToolCallId") in completed_calls
                )
            ):
                self._unreliable.add(physical)
                continue
            prev = self._children.get(cid)
            if prev and any(
                prev.get(key) and item.get(key) and prev[key] != item[key]
                for key in ("parentSubagentRunId", "parentToolCallId")
            ):
                self._unreliable.add(physical)
                continue
            child = {**item, "runId": public_id, "physicalRunId": str(physical)}
            if prev:
                for key in ("parentSubagentRunId", "parentToolCallId", "parentMessageId"):
                    if key in prev and key not in child:
                        child[key] = prev[key]
            self._children[cid] = child
            accepted.add(cid)
        return accepted

    def finish(self) -> AssembledSnapshot:
        messages: list[Row] = []
        links: list[AgUiSnapshotMessageLink] = []
        visible_ids = {entry.message["id"] for group in self._extras.values() for entry in group}
        visible_checkpoints = {
            entry.link.checkpoint_id
            for entry in self._checkpoint
            if not self._require_run or entry.link.physical_run_id in self._runs
        }
        visible_ids.update(
            entry.message["id"]
            for entry in self._checkpoint
            if not self._require_run or entry.link.physical_run_id in self._runs
        )
        tip: str | None = None
        for slot in range(len(self._checkpoint) + 1):
            entries = list(self._extras.get(slot, ()))
            if slot < len(self._checkpoint):
                saved = self._checkpoint[slot]
                if not self._require_run or saved.link.physical_run_id in self._runs:
                    entries.append(saved)
            for entry in entries:
                message, link = entry.message, entry.link
                root = message["role"] in ("assistant", "user") and not link.subagent_run_id
                parent = link.parent_message_id
                if parent:
                    parent = self._aliases.get(parent, parent)
                if parent not in visible_ids:
                    parent = tip if root else None
                updates: Row = {}
                if parent != link.parent_message_id:
                    updates["parent_message_id"] = parent
                if self._require_run:
                    if link.parent_checkpoint_id not in visible_checkpoints:
                        updates["parent_checkpoint_id"] = None
                    updates["sibling_checkpoint_ids"] = [
                        sibling
                        for sibling in link.sibling_checkpoint_ids
                        if sibling in visible_checkpoints
                    ]
                if updates:
                    link = link.model_copy(update=updates)
                if root:
                    tip = message["id"]
                links.append(link)
                messages.append(message)
        children = []
        for child in self._children.values():
            item = child.copy()
            if parent_id := item.get("parentMessageId"):
                parent_id = self._aliases.get(parent_id, parent_id)
                if parent_id in visible_ids:
                    item["parentMessageId"] = parent_id
                else:
                    item.pop("parentMessageId")
            children.append(item)
        return AssembledSnapshot(messages, links, children, self._todos, tip, self._unreliable)
