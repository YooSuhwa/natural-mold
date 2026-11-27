"""Restrict checkpoint tool calls and child controls to currently visible issuers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import chain
from typing import Any

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


def filter_checkpoint_calls(
    checkpoint: Sequence[LinkedMessage],
    extra: Sequence[LinkedMessage],
    visibility_by_run: Mapping[str, ChildVisibility],
) -> dict[str, Row]:
    """Keep root transcript text while dropping denied delegated calls and their results."""
    by_call = _issuers(chain(checkpoint, extra))
    safe_calls: set[CallKey] = set()
    scoped_calls = {(lane, cid) for run_id, lane, cid in by_call if run_id is not None}
    for key, issuers in by_call.items():
        if len(issuers) != 1:
            continue
        run_id, _, cid = key
        _, name, _ = next(iter(issuers))
        grant = visibility_by_run.get(run_id) if run_id is not None else None
        if name != DELEGATION_TOOL or (
            grant and (grant.allow_legacy_details or cid in grant.tool_call_ids)
        ):
            safe_calls.add(key)

    result: dict[str, Row] = {}
    for row, link in checkpoint:
        if row["id"] != link.message_id or row.get("subagentRunId") != link.subagent_run_id:
            continue
        scope = link.physical_run_id, link.subagent_run_id
        if row["role"] == "tool":
            # A result has no trustworthy tool name of its own. In particular,
            # an unbound or ambiguous call ID cannot establish a non-delegation.
            cid = row.get("toolCallId")
            if (*scope, cid) not in safe_calls or (
                link.physical_run_id is None and (link.subagent_run_id, cid) in scoped_calls
            ):
                continue
        elif row["role"] == "assistant" and row.get("toolCalls"):
            visible = [call for cid, _, call in _calls(row) if (*scope, cid) in safe_calls]
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


def visible_child_controls(
    children: Sequence[Row],
    messages: Sequence[Row],
    links: Sequence[AgUiSnapshotMessageLink],
) -> list[Row]:
    """Expose a namespace-verified task handle only beside its actual public delegation."""
    by_call = _issuers(zip(messages, links, strict=True))
    public_runs = {(link.physical_run_id, link.message_id): link.run_id for link in links}
    result = []
    for child in children:
        if child.get("childTaskId"):
            physical = child.get("physicalRunId")
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
                    and (not child.get("runId") or not public or child["runId"] == public)
                ):
                    result.append(child)
                    continue
            child = child.copy()
            child.pop("childTaskId")
        result.append(child)
    return result
