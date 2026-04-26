from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.schemas.conversation import MessageResponse

_TYPE_TO_ROLE = {"human": "user", "ai": "assistant", "tool": "tool"}


def _parse_msg_id(raw_id: str | None, conversation_id: uuid.UUID, idx: int) -> uuid.UUID:
    if not raw_id:
        return uuid.uuid5(conversation_id, str(idx))
    try:
        return uuid.UUID(raw_id)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, raw_id)


def _content_to_text(content: Any) -> str:
    """LangChain BaseMessage.content를 사용자 표시용 plain text로 변환.

    Anthropic은 multi-block content (text + tool_use 등)를 list[dict]로 반환.
    text 블록만 concat하고 tool_use 블록은 무시 (tool_calls 필드로 별도 노출).
    그 외 비-list/dict 형태는 안전하게 str() fallback.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def langchain_messages_to_response(
    messages: list[BaseMessage],
    conversation_id: uuid.UUID,
    base_timestamp: datetime | None = None,
) -> list[MessageResponse]:
    """LangChain BaseMessage 리스트를 MessageResponse 리스트로 변환."""
    results: list[MessageResponse] = []
    base_ts = base_timestamp or datetime.now(UTC).replace(tzinfo=None)

    for idx, msg in enumerate(messages):
        role = _TYPE_TO_ROLE.get(msg.type, msg.type)
        content = _content_to_text(msg.content)

        results.append(
            MessageResponse(
                id=_parse_msg_id(msg.id, conversation_id, idx),
                conversation_id=conversation_id,
                role=role,
                content=content,
                tool_calls=getattr(msg, "tool_calls", None) or None,
                tool_call_id=getattr(msg, "tool_call_id", None),
                created_at=base_ts + timedelta(milliseconds=idx),
            )
        )

    return results


def convert_to_langchain_messages(messages: list[dict[str, str]]) -> list[BaseMessage]:
    lc_messages: list[BaseMessage] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))
    return lc_messages


_JSON_BLOCK_RE = re.compile(r"```json\s*([\s\S]*?)\s*```")


def extract_json_from_markdown(content: str) -> dict[str, Any] | None:
    """Extract and merge all JSON objects from markdown code blocks."""
    matches = _JSON_BLOCK_RE.findall(content)
    if not matches:
        return None
    merged: dict[str, Any] = {}
    for raw in matches:
        try:
            merged.update(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return merged if merged else None


def strip_json_blocks(content: str) -> str:
    """Remove JSON code blocks from displayed message content."""
    return _JSON_BLOCK_RE.sub("", content).strip()
