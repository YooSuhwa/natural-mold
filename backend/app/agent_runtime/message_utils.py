from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.schemas.conversation import MessageResponse, TokenUsageBreakdown

_TYPE_TO_ROLE = {"human": "user", "ai": "assistant", "tool": "tool"}


def parse_msg_id(raw_id: str | None, conversation_id: uuid.UUID, idx: int) -> uuid.UUID:
    if not raw_id:
        return uuid.uuid5(conversation_id, str(idx))
    try:
        return uuid.UUID(raw_id)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, raw_id)


def content_to_text(content: Any) -> str:
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
    timestamps: list[datetime] | None = None,
) -> list[MessageResponse]:
    """LangChain BaseMessage 리스트를 MessageResponse 리스트로 변환.

    `timestamps`가 주어지면 메시지 idx별 시각으로 사용 (영구 매핑 우선).
    fallback은 `now() + idx*1ms` (테스트/단발 호출용).
    """
    results: list[MessageResponse] = []
    fallback_base = datetime.now(UTC).replace(tzinfo=None)

    for idx, msg in enumerate(messages):
        role = _TYPE_TO_ROLE.get(msg.type, msg.type)
        content = content_to_text(msg.content)

        if timestamps is not None and idx < len(timestamps):
            created_at = timestamps[idx]
        else:
            created_at = fallback_base + timedelta(milliseconds=idx)

        # W7 — AIMessage가 들고 다니는 ``usage_metadata``를 평탄화. user/tool
        # 메시지는 None. cache_* 필드가 없으면 0으로 채움.
        usage = _extract_usage(msg)

        results.append(
            MessageResponse(
                id=parse_msg_id(msg.id, conversation_id, idx),
                conversation_id=conversation_id,
                role=role,
                content=content,
                tool_calls=getattr(msg, "tool_calls", None) or None,
                tool_call_id=getattr(msg, "tool_call_id", None),
                created_at=created_at,
                usage=usage,
            )
        )

    return results


def _extract_usage(msg: BaseMessage) -> TokenUsageBreakdown | None:
    """LangChain ``usage_metadata``를 ``TokenUsageBreakdown``으로 평탄화.

    streaming.py의 ``message_end`` 발행 로직과 동일한 평탄화를 fetch 경로에서
    재사용. user/tool 메시지나 usage_metadata가 없는 chunk는 ``None``.
    """
    meta = getattr(msg, "usage_metadata", None)
    if not meta:
        return None
    input_details = meta.get("input_token_details") or {}
    prompt = int(meta.get("input_tokens", 0))
    completion = int(meta.get("output_tokens", 0))
    cache_creation = int(input_details.get("cache_creation", 0))
    cache_read = int(input_details.get("cache_read", 0))
    if prompt == 0 and completion == 0 and cache_creation == 0 and cache_read == 0:
        return None
    return TokenUsageBreakdown(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
    )


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
