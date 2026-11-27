"""Insert display-only, pre-run turns into a conversation's public transcript."""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from app.models.conversation_run import ConversationRun
from app.schemas.ag_ui_snapshot import AgUiSnapshotMessageLink
from app.services.ag_ui_snapshot_assembly import AssembledSnapshot, Row

type Pair = tuple[Row, AgUiSnapshotMessageLink]


def _identity(value: str) -> str:
    # A UUID may have been serialized with capitals, braces or without hyphens.
    try:
        return str(UUID(value))
    except ValueError:
        return value


def _clock(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=UTC)
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _reserved_ids(merged: AssembledSnapshot, accepted: Mapping[UUID, ConversationRun]) -> set[str]:
    ids: set[str] = {str(run_id) for run_id in accepted}
    for row, link in zip(merged.messages, merged.message_links, strict=True):
        values = (
            row["id"],
            link.message_id,
            link.ui_message_id,
            link.run_id,
            link.physical_run_id,
            link.parent_message_id,
            link.checkpoint_id,
            link.parent_checkpoint_id,
            link.subagent_run_id,
            link.parent_tool_call_id,
            row.get("toolCallId"),
            *link.sibling_checkpoint_ids,
            *(call.get("id") for call in row.get("toolCalls", [])),
        )
        ids.update(_identity(value) for value in values if isinstance(value, str))
    for child in merged.subagents:
        ids.update(
            _identity(child[key])
            for key in (
                "subagentRunId",
                "parentSubagentRunId",
                "childTaskId",
                "parentToolCallId",
                "parentMessageId",
                "runId",
                "physicalRunId",
            )
            if isinstance(child.get(key), str)
        )
    return ids


def _old_rows(pairs: Sequence[Pair], reserved: set[str]) -> list[Pair]:
    # A reused client ID cannot identify which historical turn should survive.
    counts = Counter(_identity(link.message_id) for _, link in pairs)
    visible_users: dict[str, datetime | None] = {}
    clean: list[Pair] = []
    for row, link in pairs:
        mid = link.message_id
        key = _identity(mid)
        role, content = row.get("role"), row.get("content")
        if (
            not mid
            or row.get("id") != mid
            or key in reserved
            or counts[key] != 1
            or role not in ("assistant", "user")
            or not isinstance(content, str)
            or row.get("subagentRunId")
            or link.subagent_run_id
        ):
            continue
        at = link.created_at
        parent = link.parent_message_id
        if role == "user":
            visible_users[mid] = at
        elif parent is not None:
            if parent not in visible_users:
                continue
            # The producer links messages within the same persisted turn. A turn
            # remains adjacent even when only one message has a recorded time.
            at = visible_users[parent]
        clean.append(
            (
                {"id": mid, "role": role, "content": content},
                AgUiSnapshotMessageLink(message_id=mid, created_at=at),
            )
        )
    return sorted(clean, key=lambda pair: _clock(pair[1].created_at))


def _turn_starts(
    merged: AssembledSnapshot, accepted: Mapping[UUID, ConversationRun]
) -> tuple[list[int], list[datetime], int]:
    runs = {str(key): run for key, run in accepted.items()}
    last_position = {
        link.physical_run_id: index
        for index, link in enumerate(merged.message_links)
        if link.physical_run_id
    }
    starts: list[int] = []
    dates: list[datetime | None] = []
    active_start = len(merged.messages)
    end = -1
    for index, link in enumerate(merged.message_links):
        rid = link.physical_run_id
        run = runs.get(rid) if rid else None
        if index > end:
            starts.append(index)
            dates.append((run.created_at if run else None) or link.created_at)
        if run and run.is_active:
            active_start = min(starts[-1], active_start)
        end = max(end, last_position.get(rid, index))
    # An undated physical turn stays beside the following dated one, or after
    # the historical transcript when there is no following timestamp.
    clocks = [datetime.max.replace(tzinfo=UTC)] * len(dates)
    for index in reversed(range(len(dates))):
        if dates[index] is not None:
            clocks[index] = _clock(dates[index])
        elif index + 1 < len(dates):
            clocks[index] = clocks[index + 1]
    for index in range(1, len(clocks)):
        clocks[index] = max(clocks[index - 1], clocks[index])
    if (
        starts
        and active_start == len(merged.messages)
        and any(run.is_active for run in runs.values())
    ):
        # A claimed run may have emitted no public frame yet.
        active_start = starts[-1]
    return starts, clocks, active_start


def insert_legacy_transcript(
    merged: AssembledSnapshot,
    old_pairs: Sequence[Pair],
    accepted: Mapping[UUID, ConversationRun],
) -> AssembledSnapshot:
    """Keep physical turns atomic and insert old text without granting run authority."""
    if not old_pairs:
        return merged
    old = _old_rows(old_pairs, _reserved_ids(merged, accepted))
    if not old:
        return merged
    starts, clocks, active_start = _turn_starts(merged, accepted)
    slots: dict[int, list[Pair]] = defaultdict(list)
    for pair in old:
        position = bisect_left(clocks, _clock(pair[1].created_at))
        slot = starts[position] if position < len(starts) else len(merged.messages)
        # A migrated clock cannot displace the current execution's active head.
        slots[min(slot, active_start)].append(pair)

    messages: list[Row] = []
    links: list[AgUiSnapshotMessageLink] = []
    canonical_parent = previous = None
    for index in range(len(merged.messages) + 1):
        for row, link in slots.get(index, ()):
            messages.append(row)
            links.append(link.model_copy(update={"parent_message_id": previous}))
            previous = row["id"]
        if index < len(merged.messages):
            row, link = merged.messages[index], merged.message_links[index]
            if row["role"] in ("user", "assistant") and not link.subagent_run_id:
                # Splice the linear lineage. Explicit links to other branches and
                # parent links that attach tool/child messages retain their targets.
                if link.parent_message_id == canonical_parent and previous != canonical_parent:
                    link = link.model_copy(update={"parent_message_id": previous})
                canonical_parent = previous = row["id"]
            messages.append(row)
            links.append(link)
    tip = merged.active_tip_message_id
    if tip is None or tip == canonical_parent:
        tip = previous
    return replace(merged, messages=messages, message_links=links, active_tip_message_id=tip)
