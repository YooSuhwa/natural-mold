from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_agent_response(
    agent: Any,
    messages: list[Any],
    config: dict[str, Any],
) -> AsyncGenerator[str, None]:
    msg_id = str(uuid.uuid4())

    yield format_sse("message_start", {"id": msg_id, "role": "assistant"})

    full_content = ""
    usage_data: dict[str, int] = {}

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
                    full_content += delta
                    yield format_sse("content_delta", {"delta": delta})

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

    yield format_sse("message_end", {"usage": usage_data, "content": full_content})
