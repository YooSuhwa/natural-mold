from __future__ import annotations

from collections.abc import Mapping

from app.agent_runtime.protocol_events import StoredProtocolEvent


def collect_root_ai_message_id(
    event: StoredProtocolEvent,
    sink: list[str] | None,
) -> None:
    """Collect a current root AI message identity from a normalized stream event."""
    data = event["data"]
    if (
        sink is None
        or event["method"] != "messages"
        or event["namespace"]
        or not isinstance(data, Mapping)
    ):
        return
    raw_id = data.get("id")
    is_v3_ai_start = data.get("event") == "message-start" and data.get("role") == "ai"
    is_legacy_ai = (
        "event" not in data and "role" not in data and data.get("type") in {"ai", "AIMessageChunk"}
    )
    if not is_v3_ai_start and not is_legacy_ai:
        return
    if isinstance(raw_id, str) and raw_id and raw_id not in sink:
        sink.append(raw_id)
