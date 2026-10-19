"""Protocol ``run.start`` input messages as LangChain messages (split from the fork module)."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


def messages_from_protocol_input(input_payload: dict[str, Any]) -> list[BaseMessage]:
    messages = input_payload.get("messages")
    if not isinstance(messages, list):
        return []

    converted: list[BaseMessage] = []
    for raw in messages:
        if not isinstance(raw, dict):
            continue
        msg = message_from_protocol_mapping(raw)
        if msg is not None:
            converted.append(msg)
    return converted


def message_from_protocol_mapping(raw: dict[str, Any]) -> BaseMessage | None:
    role = raw.get("role") or raw.get("type")
    content = raw.get("content", "")
    if not isinstance(content, str | list):
        content = str(content)

    message_id = raw.get("id")
    kwargs = {"id": message_id} if isinstance(message_id, str) and message_id else {}

    if role in {"human", "user"}:
        return HumanMessage(content=content, **kwargs)
    if role in {"ai", "assistant"}:
        return AIMessage(content=content, **kwargs)
    if role == "system":
        return SystemMessage(content=content, **kwargs)
    return None
