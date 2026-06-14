from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from app.schemas.conversation import MessageResponse, TokenUsageBreakdown

_TYPE_TO_ROLE = {"human": "user", "ai": "assistant", "tool": "tool"}
_PRIVATE_REASONING_BLOCK_TYPES = frozenset(
    {
        "reasoning",
        "reasoning_content",
        "thinking",
        "redacted_thinking",
    }
)


def parse_msg_id(raw_id: str | None, conversation_id: uuid.UUID, idx: int) -> uuid.UUID:
    if not raw_id:
        return uuid.uuid5(conversation_id, str(idx))
    try:
        return uuid.UUID(raw_id)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, raw_id)


def _content_block_to_display_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return ""

    block_type = block.get("type")
    if isinstance(block_type, str) and block_type in _PRIVATE_REASONING_BLOCK_TYPES:
        return ""

    if block_type == "text":
        text = block.get("text")
        if isinstance(text, str):
            return text
    return ""


def content_to_text(content: Any) -> str:
    """LangChain BaseMessage.content를 사용자 표시용 plain text로 변환.

    Anthropic은 multi-block content (text + tool_use 등)를 list[dict]로 반환.
    text 블록만 concat하고 tool_use 블록은 무시 (tool_calls 필드로 별도 노출).
    provider-private reasoning/thinking 블록은 SSE/DB 표시 상태로 들어가지 않게
    여기서 제거한다. 그 외 비-list/dict 형태는 안전하게 str() fallback.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            parts.append(_content_block_to_display_text(block))
        return "".join(parts)
    if isinstance(content, dict):
        return _content_block_to_display_text(content)
    return str(content)


def langchain_messages_to_response(
    messages: list[BaseMessage],
    conversation_id: uuid.UUID,
    timestamps: list[datetime] | None = None,
    *,
    cost_per_input_token: float | None = None,
    cost_per_output_token: float | None = None,
) -> list[MessageResponse]:
    """LangChain BaseMessage 리스트를 MessageResponse 리스트로 변환.

    `timestamps`가 주어지면 메시지 idx별 시각으로 사용 (영구 매핑 우선).
    fallback은 `now() + idx*1ms` (테스트/단발 호출용).

    ``cost_per_*_token`` (W7-4): conversation의 agent에 연결된 model 단가를
    호출자가 넘기면 ``MessageResponse.usage.estimated_cost``를 메시지마다 계산.
    cache_read는 일반적으로 input의 10% 단가지만 모델별로 다르므로 본 구현은
    cache_creation을 input과 동일 단가로, cache_read를 input의 10% 단가로
    근사한다 (Anthropic prompt caching 기본값).
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
        usage = extract_usage_breakdown(
            msg,
            cost_per_input_token=cost_per_input_token,
            cost_per_output_token=cost_per_output_token,
        )

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


def extract_usage_breakdown(
    msg: BaseMessage,
    *,
    cost_per_input_token: float | None = None,
    cost_per_output_token: float | None = None,
) -> TokenUsageBreakdown | None:
    """LangChain ``usage_metadata``를 ``TokenUsageBreakdown``으로 평탄화.

    streaming.py의 ``message_end`` 발행 로직과 동일한 평탄화를 fetch 경로에서
    재사용. user/tool 메시지나 usage_metadata가 없는 chunk는 ``None``.

    단가가 주어지면 ``estimated_cost`` 도 함께 채운다. ``input_tokens``는
    LangChain 1.x에서 cache 토큰을 모두 포함한 총 input이므로 단순히
    ``prompt × cost_per_input + completion × cost_per_output``로 계산.
    cache_read는 정확한 단가가 다를 수 있으나 fetch 경로의 표시값은 근사로
    충분 (정확한 누적은 Daily Spend / token_usages가 별도 path로 추적).
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

    estimated_cost: float | None = None
    if cost_per_input_token is not None or cost_per_output_token is not None:
        cost = (prompt * (cost_per_input_token or 0)) + (
            completion * (cost_per_output_token or 0)
        )
        estimated_cost = round(cost, 8) if cost > 0 else 0.0

    return TokenUsageBreakdown(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        estimated_cost=estimated_cost,
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
