from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _is_tool_selector_json(text: str) -> bool:
    """Check if text is LLMToolSelectorMiddleware output like {"tools":[...]}.

    ADR-004: PatchToolCallsMiddleware는 before_agent() 훅만 구현.
    스트림 이벤트를 필터링하지 않으므로 이 필터가 여전히 필요.

    Strict: only matches when "tools" is the sole key to avoid
    false positives on legitimate agent JSON output.
    """
    try:
        parsed = json.loads(text)
        return (
            isinstance(parsed, dict)
            and set(parsed.keys()) == {"tools"}
            and isinstance(parsed["tools"], list)
        )
    except (json.JSONDecodeError, ValueError):
        return False


async def stream_agent_response(
    agent: Any,
    messages: list[Any],
    config: dict[str, Any],
    *,
    cost_per_input_token: float | None = None,
    cost_per_output_token: float | None = None,
) -> AsyncGenerator[str, None]:
    msg_id = str(uuid.uuid4())

    yield format_sse("message_start", {"id": msg_id, "role": "assistant"})

    full_content = ""
    usage_data: dict[str, int] = {}
    # ADR-004: PatchToolCallsMiddleware가 스트림 필터링을 하지 않으므로
    # character-by-character 버퍼링으로 미들웨어 JSON을 감지/제거.
    _buf = ""
    _brace_depth = 0

    try:
        async for chunk in agent.astream(
            {"messages": messages},
            config=config,
            stream_mode="messages",
        ):
            msg, metadata = chunk
            if hasattr(msg, "content") and msg.content and msg.type in ("ai", "AIMessageChunk"):
                delta = msg.content
                if isinstance(delta, str):
                    for ch in delta:
                        if ch == "{" and _brace_depth == 0:
                            # Start buffering potential middleware JSON
                            _brace_depth = 1
                            _buf = ch
                        elif _brace_depth > 0:
                            _buf += ch
                            if ch == "{":
                                _brace_depth += 1
                            elif ch == "}":
                                _brace_depth -= 1
                                if _brace_depth == 0:
                                    # Outermost brace closed — check if middleware output
                                    if _is_tool_selector_json(_buf):
                                        _buf = ""
                                    else:
                                        full_content += _buf
                                        yield format_sse("content_delta", {"delta": _buf})
                                        _buf = ""
                        else:
                            full_content += ch
                            yield format_sse("content_delta", {"delta": ch})

            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    yield format_sse(
                        "tool_call_start",
                        {
                            "tool_name": tc.get("name", ""),
                            "parameters": tc.get("args", {}),
                        },
                    )

            if msg.type == "tool":
                yield format_sse(
                    "tool_call_result",
                    {
                        "tool_name": msg.name if hasattr(msg, "name") else "",
                        "result": msg.content if isinstance(msg.content, str) else str(msg.content),
                    },
                )

            if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                usage_data = {
                    "prompt_tokens": msg.usage_metadata.get("input_tokens", 0),
                    "completion_tokens": msg.usage_metadata.get("output_tokens", 0),
                }

    except Exception as e:
        yield format_sse("error", {"message": str(e)})

    # Flush any remaining buffer (incomplete JSON = not middleware output)
    if _buf:
        full_content += _buf

    # Calculate estimated cost from model pricing if available
    if usage_data and (cost_per_input_token or cost_per_output_token):
        prompt = usage_data.get("prompt_tokens", 0)
        completion = usage_data.get("completion_tokens", 0)
        cost = (prompt * (cost_per_input_token or 0)) + (completion * (cost_per_output_token or 0))
        usage_data["estimated_cost"] = round(cost, 8)

    yield format_sse("message_end", {"usage": usage_data, "content": full_content})
