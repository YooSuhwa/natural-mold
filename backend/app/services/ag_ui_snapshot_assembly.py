"""Reconcile the selected checkpoint with safe views of its authorized runs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.services.ag_ui_snapshot_call_visibility import (
    filter_checkpoint_calls,
    visible_child_controls,
)
from app.services.ag_ui_snapshot_idless import match_idless_run_text
from app.services.ag_ui_snapshot_matching import ScopedCheckpointCalls

type Row = dict[str, Any]


class _Replay(Protocol):
    @property
    def visibility(self) -> ChildVisibility: ...
    @property
    def root_issuances(self) -> Mapping[tuple[str | None, str], str]: ...
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
        idless_checkpoint_ids: Collection[str] = (),
        thread_id: str | None = None,
    ) -> None:
        self._legacy = include_legacy_users and not checkpoint
        self._require_run = require_run_link
        self._checkpoint = [_Entry(deepcopy(row), link) for row, link in checkpoint]
        self._thread_id = thread_id
        self._raw_tool_rows: dict[str | None, list[tuple[int, Row, AgUiSnapshotMessageLink]]] = (
            defaultdict(list)
        )
        self._turns: list[int] = []
        self._unbound_call_turns: dict[int, list[tuple[int, Row, AgUiSnapshotMessageLink]]] = (
            defaultdict(list)
        )
        self._projected_calls: set[int] = set()
        self._hidden: set[int] = set()
        self._idless_ids = frozenset(idless_checkpoint_ids)
        self._visibility: dict[str, ChildVisibility] = {}
        self._by_id: dict[str, list[int]] = defaultdict(list)
        self._bound: dict[str, list[int]] = defaultdict(list)
        self._occupied: set[str] = set()
        self._extras: dict[int, list[_Entry]] = defaultdict(list)
        self._aliases: dict[str, str] = {}
        self._runs: set[str] = set()
        self._unreliable: set[UUID] = set()
        self._children: dict[str, Row] = {}
        self._todos: dict[str | None, list[Row]] = {}
        turn = 0
        for index, entry in enumerate(self._checkpoint):
            row = entry.message
            if row["role"] == "user":
                turn += 1
            self._turns.append(turn)
            mid = row["id"]
            self._by_id[mid].append(index)
            self._occupied.add(mid)
            if entry.link.physical_run_id:
                self._bound[str(entry.link.physical_run_id)].append(index)
            if row.get("toolCalls") or row["role"] == "tool":
                source = (index, row, entry.link)
                self._raw_tool_rows[entry.link.physical_run_id].append(source)
                if not entry.link.physical_run_id:
                    self._unbound_call_turns[turn].append(source)

    def _candidate_calls(
        self, physical: str | None, public_id: str, replay: _Replay
    ) -> ScopedCheckpointCalls:
        owned = self._bound.get(physical, ()) if physical else ()
        turns = {self._turns[i] for i in owned}
        # A provider call ID can recur in many turns. Only examine old unstamped
        # calls alongside an actual root message or a checkpoint stamped for this run.
        for message in replay.messages:
            if not message.get("subagentRunId") and isinstance(mid := message.get("id"), str):
                turns.update(self._turns[i] for i in self._by_id.get(mid, ()))
        raw = [
            item
            for turn in turns
            for item in self._unbound_call_turns.get(turn, ())
            if item[0] not in self._projected_calls
        ]
        if physical:
            for index, row, link in self._raw_tool_rows.get(physical, ()):
                if link.run_id is None:
                    # The supplied public ID was resolved for this exact physical run.
                    link = link.model_copy(update={"run_id": public_id})
                raw.append((index, row, link))
        raw.sort(key=lambda item: item[0])
        return ScopedCheckpointCalls(
            raw, public_id=public_id, visibility=replay.visibility, issuances=replay.root_issuances
        )

    def _record_call(self, index: int, calls: ScopedCheckpointCalls) -> None:
        if index not in calls.rows or index in self._projected_calls:
            return
        self._projected_calls.add(index)
        entry = self._checkpoint[index]
        raw_id = entry.message["id"]
        safe = calls.rows[index]
        if safe is None or (safe["id"] != raw_id and safe["id"] in self._occupied):
            self._hidden.add(index)
            return
        entry.message = safe
        if safe["id"] != raw_id:
            self._aliases[raw_id] = safe["id"]
            self._occupied.add(safe["id"])
            entry.link = entry.link.model_copy(update={"message_id": safe["id"]})

    def _match(
        self, row: Mapping[str, Any], physical_id: str | None, calls: ScopedCheckpointCalls
    ) -> int | None:
        if row.get("subagentRunId"):
            return None
        role = row.get("role")
        mid = row.get("id")
        if not isinstance(mid, str) or role not in ("assistant", "tool", "user"):
            return None
        indices = calls.by_id.get(mid) or self._by_id.get(mid)
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
                    candidates.update(calls.by_call.get((role, key), ()))
            if len(candidates) != 1:
                return None
            index = candidates.pop()
        entry = self._checkpoint[index]
        saved = calls.rows.get(index, entry.message)
        if saved is None:
            return None
        known_owner = entry.link.physical_run_id
        if saved["role"] != role or (
            known_owner and physical_id and str(known_owner) != physical_id
        ):
            return None
        if role == "assistant":
            saved_text, text = saved.get("content"), row.get("content")
            if (
                isinstance(saved_text, str)
                and isinstance(text, str)
                and not (saved_text.startswith(text) or text.startswith(saved_text))
            ):
                return None
            saved_calls, public_calls = _call_ids(saved), _call_ids(row)
            if saved_calls and public_calls and saved_calls.isdisjoint(public_calls):
                return None
        if role == "tool" and row.get("toolCallId") != saved.get("toolCallId"):
            return None
        if role == "user" and row.get("content") != saved.get("content"):
            return None
        return index

    def has_root_match(
        self,
        replay: _Replay,
        physical_run_id: UUID | None = None,
        *,
        public_run_id: str | None = None,
    ) -> bool:
        """An anchor on this branch establishes a prefix, never a whole historic fork."""
        physical = str(physical_run_id) if physical_run_id else None
        calls = self._candidate_calls(physical, public_run_id or physical or "", replay)
        return any(
            row.get("role") in ("assistant", "tool")
            and self._match(row, physical, calls) is not None
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
        self._visibility[physical] = replay.visibility
        rows = [*replay.legacy_users, *replay.messages]
        calls = self._candidate_calls(physical, public_run_id, replay)
        matches = [self._match(row, physical, calls) for row in rows]
        bound = self._bound.get(physical, [])
        for index in bound:
            self._record_call(index, calls)
        ambiguous = match_idless_run_text(
            rows,
            matches,
            {i: self._checkpoint[i].message for i in bound} if self._idless_ids else {},
            self._idless_ids,
        )
        anchors = [index for index in matches if index is not None]
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
            if row_number in ambiguous:
                self._unreliable.add(physical_run_id)
                continue
            mid = row.get("id")
            if not isinstance(mid, str) or not mid:
                self._unreliable.add(physical_run_id)
                continue
            meta = replay.message_links.get(mid, {})
            if index is not None:
                self._record_call(index, calls)
                if (
                    index in seen
                    or index in self._hidden
                    or not self._merge_checkpoint(
                        index, row, public_run_id, physical, restrict_to_checkpoint, meta
                    )
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
                prev.pop("childTaskId", None)
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

    def _finish_unbound_calls(self) -> None:
        # A history without run receipts owns no protected task. For regular
        # tools it still has a thread-scoped identity and a chronological issuer.
        for physical, raw in self._raw_tool_rows.items():
            pending = [item for item in raw if item[0] not in self._projected_calls]
            if not pending:
                continue
            scope = pending[0][2].run_id or (
                "checkpoint:" + self._thread_id if self._thread_id else None
            )
            if not scope:
                self._hidden.update(index for index, _, _ in pending)
                # Keep public assistant text even without any identity for its tools.
                for index, row, _ in pending:
                    if row["role"] == "assistant" and row.get("content"):
                        self._hidden.discard(index)
                        self._checkpoint[index].message = {
                            k: v for k, v in row.items() if k != "toolCalls"
                        }
                continue
            calls = ScopedCheckpointCalls(
                pending,
                public_id=scope,
                visibility=self._visibility.get(physical) if physical else None,
                issuances={},
            )
            for index, _, _ in pending:
                self._record_call(index, calls)

    def finish(self) -> AssembledSnapshot:
        self._finish_unbound_calls()
        messages: list[Row] = []
        links: list[AgUiSnapshotMessageLink] = []
        extras = [(e.message, e.link) for group in self._extras.values() for e in group]
        confirmed = [
            (e.message, e.link)
            for index, e in enumerate(self._checkpoint)
            if index not in self._hidden
            and (not self._require_run or e.link.physical_run_id in self._runs)
        ]
        checkpoint = filter_checkpoint_calls(confirmed, extras)
        visible_ids = {row["id"] for row, _ in extras} | checkpoint.keys()
        visible_calls = {
            (link.physical_run_id, cid) for row, link in extras for cid in _call_ids(row)
        }
        visible_calls.update(
            (link.physical_run_id, cid)
            for row, link in confirmed
            for cid in _call_ids(checkpoint.get(row["id"], {}))
        )
        visible_checkpoints = {
            link.checkpoint_id for row, link in confirmed if row["id"] in checkpoint
        }
        tip: str | None = None
        for slot in range(len(self._checkpoint) + 1):
            entries = list(self._extras.get(slot, ()))
            if slot < len(self._checkpoint) and slot not in self._hidden:
                saved = self._checkpoint[slot]
                safe = checkpoint.get(saved.message["id"])
                if safe is not None:
                    entries.append(_Entry(safe, saved.link))
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
                if (
                    link.parent_tool_call_id
                    and (link.physical_run_id, link.parent_tool_call_id) not in visible_calls
                ):
                    updates["parent_tool_call_id"] = None
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
                item["parentMessageId"] = parent_id
                if parent_id not in visible_ids:
                    item.pop("parentMessageId")
                    item.pop("childTaskId", None)
            children.append(item)
        return AssembledSnapshot(
            messages,
            links,
            visible_child_controls(children, messages, links),
            self._todos,
            tip,
            self._unreliable,
        )
