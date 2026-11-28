"""Restrict checkpoint tool calls and child controls to currently visible issuers."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import chain
from typing import Any

from app.agent_runtime.ag_ui_v1_children import PublicChildren, public_subagent_id
from app.agent_runtime.ag_ui_v1_issuance import public_tool_call_id, public_tool_result_id
from app.agent_runtime.ag_ui_visibility import ChildVisibility
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.tools.keys import DELEGATION_TOOL

type Row = dict[str, Any]
type LinkedMessage = tuple[Row, AgUiSnapshotMessageLink]
type CallKey = tuple[str | None, str | None, str]
type Issuer = tuple[str, str, int]


def _calls(row: Row) -> Iterable[tuple[str, str, Row]]:
    calls = row.get("toolCalls")
    if not isinstance(calls, list):
        return
    for call in calls:
        if not isinstance(call, dict) or not isinstance(function := call.get("function"), dict):
            continue
        cid, name = call.get("id"), function.get("name")
        if isinstance(cid, str) and cid and isinstance(name, str) and name:
            yield cid, name, call


def _issuers(messages: Iterable[LinkedMessage]) -> dict[CallKey, set[Issuer]]:
    by_call: dict[CallKey, set[Issuer]] = defaultdict(set)
    for message, link in messages:
        if (
            message["role"] != "assistant"
            or message["id"] != link.message_id
            or message.get("subagentRunId") != link.subagent_run_id
        ):
            continue
        counts: dict[str, int] = defaultdict(int)
        for cid, name, _ in _calls(message):
            key = link.physical_run_id, link.subagent_run_id, cid
            by_call[key].add((link.message_id, name, counts[cid]))
            counts[cid] += 1
    return by_call


def canonical_checkpoint_calls(
    checkpoint: Sequence[LinkedMessage],
    *,
    public_run_id: str,
    visibility: ChildVisibility | None,
    root_issuances: Mapping[tuple[str | None, str], str],
) -> dict[str, Row]:
    """Convert raw root calls once, in checkpoint order, retaining only proved public identities.

    The return keys are the original checkpoint message IDs. A tool result's value has the
    same issuance-based message ID as the live protocol, even when its provider supplied an ID.
    """
    # Checkpoints here are root-only. An empty thread ID cannot affect whether a
    # namespace's issuing owner is root, and no child ID is generated here.
    policy = PublicChildren("", visibility) if visibility is not None else None
    issued: dict[str, list[tuple[str | None, str]]] = defaultdict(list)
    for (parent, raw_id), opaque in root_issuances.items():
        issued[raw_id].append((parent, opaque))
    count = Counter(
        cid
        for row, _ in checkpoint
        if row.get("role") == "assistant" and not row.get("subagentRunId")
        for cid in {raw_id for raw_id, _, _ in _calls(row)}
    )
    pending: dict[str, tuple[int, str | None]] = {}
    emitted: set[str] = set()
    result: dict[str, Row] = {}
    for row, link in checkpoint:
        mid = row["id"]
        if mid != link.message_id or link.subagent_run_id is not None or row.get("subagentRunId"):
            continue
        if row["role"] == "assistant" and "toolCalls" in row:
            calls = []
            distinct: dict[str, tuple[str, Row] | None] = {}
            for raw, name, call in _calls(row):
                if raw not in distinct:
                    distinct[raw] = (name, call)
                elif distinct[raw] is not None and distinct[raw][0] != name:
                    distinct[raw] = None
            for raw, source in distinct.items():
                outstanding = pending.get(raw, (0, None))[0] + 1
                # Conflicting names and overlapping uses still consume a result.
                pending[raw] = (outstanding, None)
                if source is None:
                    continue
                name, call = source
                if name == DELEGATION_TOOL and (
                    policy is None
                    or not policy.allow_tool(name, raw)
                    or not policy.allow_scoped_tool(name, raw, None)
                ):
                    continue
                opaque = root_issuances.get((mid, raw))
                alternatives = issued.get(raw, ())
                same_run = link.physical_run_id is not None and link.run_id == public_run_id
                if opaque is None and same_run and count[raw] == 1 and len(alternatives) == 1:
                    _, opaque = alternatives[0]
                if opaque is None:
                    opaque = public_tool_call_id(public_run_id, None, mid, raw)
                if outstanding == 1:
                    pending[raw] = (1, opaque)
                calls.append({**call, "id": opaque})
            row = row.copy()
            if calls:
                row["toolCalls"] = calls
            elif row.get("content"):
                row.pop("toolCalls")
            else:
                continue
        elif row["role"] == "tool":
            raw = row.get("toolCallId")
            if not isinstance(raw, str) or not raw:
                continue
            outstanding, opaque = pending.get(raw, (0, None))
            if outstanding == 1:
                del pending[raw]
            elif outstanding > 1:
                # A call returned, but overlapping uses stay ambiguous until
                # every preceding completion has drained.
                pending[raw] = (outstanding - 1, None)
            elif not count[raw]:
                # A resume can return a call issued on a preceding physical run.
                # Its authenticated journal must identify a single continuation.
                ids = {value for _, value in issued.get(raw, ())}
                if len(ids) == 1:
                    opaque = ids.pop()
            if opaque is None or opaque in emitted:
                continue
            emitted.add(opaque)
            row = {**row, "id": public_tool_result_id(opaque), "toolCallId": opaque}
        result[mid] = row
    return result


def filter_checkpoint_calls(
    checkpoint: Sequence[LinkedMessage],
    extra: Sequence[LinkedMessage],
) -> dict[str, Row]:
    """Drop results without a visible unique issuer after branch and run reconciliation."""
    by_call = _issuers(chain(checkpoint, extra))
    issuers: dict[tuple[str | None, str], set[tuple[str | None, Issuer]]] = defaultdict(set)
    for (physical, lane, cid), origins in by_call.items():
        issuers[lane, cid].update((physical, origin) for origin in origins)
    result: dict[str, Row] = {}
    for row, link in checkpoint:
        if row["id"] != link.message_id or row.get("subagentRunId") != link.subagent_run_id:
            continue
        # The public issuance ID can identify a result on a subsequent resume.
        cid = row.get("toolCallId")
        if row["role"] == "tool" and (
            not isinstance(cid, str) or len(issuers.get((link.subagent_run_id, cid), ())) != 1
        ):
            continue
        if row["role"] == "assistant" and row.get("toolCalls"):
            visible = [
                call
                for cid, _, call in _calls(row)
                if len(issuers.get((link.subagent_run_id, cid), ())) == 1
            ]
            if len(visible) != len(row["toolCalls"]):
                row = row.copy()
                if visible:
                    row["toolCalls"] = visible
                elif row.get("content"):
                    row.pop("toolCalls")
                else:
                    continue
        result[row["id"]] = row
    return result


@dataclass(frozen=True, slots=True)
class ChildControlOrigin:
    physical: str
    public: str
    binding: tuple[str, tuple[str, ...], str]

    @classmethod
    def from_issuer(
        cls,
        child: Row,
        thread_id: str | None,
        physical: str,
        public: str,
        visibility: ChildVisibility,
    ) -> ChildControlOrigin | None:
        """Bind the original display issuer to the SQL-proved task, call and namespace."""
        handle = child.get("childTaskId")
        if not thread_id or not handle or not child.get("parentMessageId"):
            return None
        matches = [
            binding
            for binding in visibility.bindings
            if binding[2] == handle
            and binding[1]
            and public_subagent_id(thread_id, binding[1]) == child["subagentRunId"]
            and public_tool_call_id(
                public, child.get("parentSubagentRunId"), child["parentMessageId"], binding[0]
            )
            == child.get("parentToolCallId")
        ]
        return cls(physical, public, matches[0]) if len(matches) == 1 else None


def visible_child_controls(
    children: Sequence[Row],
    messages: Sequence[Row],
    links: Sequence[AgUiSnapshotMessageLink],
    origins: Mapping[str, ChildControlOrigin] | None = None,
) -> list[Row]:
    """Expose a namespace-verified task handle only beside its actual public delegation."""
    by_call = _issuers(zip(messages, links, strict=True))
    public_runs = {(link.physical_run_id, link.message_id): link.run_id for link in links}
    result = []
    for child in children:
        if child.get("childTaskId"):
            origin = (origins or {}).get(child["subagentRunId"])
            physical = origin.physical if origin else child.get("physicalRunId")
            expected_public = origin.public if origin else child.get("runId")
            cid = child.get("parentToolCallId")
            lane = child.get("parentSubagentRunId")
            issuers = by_call.get((physical, lane, cid), set()) if physical and cid else set()
            parent = child.get("parentMessageId")
            if len(issuers) == 1:
                issuer, name, _ = next(iter(issuers))
                public = public_runs.get((physical, issuer))
                if (
                    name == DELEGATION_TOOL
                    and (parent is None or parent == issuer)
                    and (
                        expected_public == public
                        if origin
                        else (not expected_public or not public or expected_public == public)
                    )
                ):
                    result.append(child)
                    continue
            child = child.copy()
            child.pop("childTaskId")
        result.append(child)
    return result
