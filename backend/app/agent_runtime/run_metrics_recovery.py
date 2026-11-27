"""Rehydrate measured root-turn activity after worker loss, without replaying old turns."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event
from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.run_metrics_baseline import (
    baseline_completed_tool_call_source_identities,
    baseline_message_identities,
)


def restore_recovery_metrics(
    metrics: RunMetricsAccumulator,
    *,
    run_id: str,
    thread_id: str,
    messages: Sequence[BaseMessage],
    events: Sequence[dict[str, Any]],
    turn_start_index: int | None = None,
) -> None:
    """The checked anchor keeps supplemental instructions inside the original turn.

    Usage events may repeat a previous conversation turn from a values snapshot.
    Admit only assistant and tool identities linked to this checked turn. Include
    provider usage saved by the graph even if the worker died before publishing
    its derived usage event. The accumulator merges cumulative snapshots by ID.
    """
    start = turn_start_index
    if start is None:
        start = next(
            i for i in reversed(range(len(messages))) if isinstance(messages[i], HumanMessage)
        )
    history = messages[:start]
    metrics.exclude_recovered_history(
        baseline_message_identities(history),
        baseline_completed_tool_call_source_identities(history),
    )
    assistants = [message for message in messages[start + 1 :] if isinstance(message, AIMessage)]
    assistant_ids = {message.id for message in assistants if message.id}
    call_ids = {call.get("id") for message in assistants for call in message.tool_calls}
    for event in events:
        if event.get("namespace") != []:
            continue
        data = event.get("data")
        if not isinstance(data, Mapping):
            continue
        if event.get("method") == "tools" and data.get("tool_call_id") in call_ids:
            metrics.observe(cast(StoredProtocolEvent, event))
        elif event.get("method") == "custom" and data.get("name") == "usage":
            payload = data.get("payload")
            if isinstance(payload, Mapping) and payload.get("assistant_msg_id") in assistant_ids:
                metrics.observe(cast(StoredProtocolEvent, event))
    for message in assistants:
        if message.id:
            metrics.observe(
                stored_protocol_event(
                    run_id=run_id,
                    thread_id=thread_id,
                    seq=0,
                    method="messages",
                    data={
                        "type": "ai",
                        "id": message.id,
                        "usage_metadata": message.usage_metadata,
                        "response_metadata": {
                            "token_usage": message.response_metadata.get("token_usage")
                        },
                    },
                )
            )
