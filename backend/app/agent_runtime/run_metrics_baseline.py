"""Content-free baseline extraction for pre-input checkpoint messages."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence

from langchain_core.messages import BaseMessage

from app.agent_runtime.run_metrics_types import MessageIdentity

type ToolCallSourceIdentity = tuple[tuple[str, ...], str, str]


def baseline_message_identities(
    messages: Iterable[BaseMessage],
) -> frozenset[MessageIdentity]:
    """Return root assistant identities without retaining checkpoint content."""
    identities: set[MessageIdentity] = set()
    for message in messages:
        if message.type != "ai" or not isinstance(message.id, str) or not message.id:
            continue
        identities.add(((), message.id))
    return frozenset(identities)


def baseline_completed_tool_call_source_identities(
    messages: Iterable[BaseMessage],
) -> frozenset[ToolCallSourceIdentity]:
    """Return completed root calls keyed by their originating assistant message."""
    materialized = tuple(messages)
    identities: set[ToolCallSourceIdentity] = set()
    pending_sources: dict[str, deque[ToolCallSourceIdentity]] = defaultdict(deque)
    for message in materialized:
        if message.type == "tool":
            tool_call_id = getattr(message, "tool_call_id", None)
            if not isinstance(tool_call_id, str) or not pending_sources[tool_call_id]:
                continue
            identities.add(pending_sources[tool_call_id].popleft())
            continue
        if message.type != "ai" or not isinstance(message.id, str) or not message.id:
            continue
        tool_calls = getattr(message, "tool_calls", None)
        if not isinstance(tool_calls, Sequence):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, Mapping):
                continue
            tool_call_id = tool_call.get("id")
            if isinstance(tool_call_id, str) and tool_call_id:
                pending_sources[tool_call_id].append(((), message.id, tool_call_id))
    return frozenset(identities)
