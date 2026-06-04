from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from app.agent_runtime.streaming import format_sse


def _parse_internal_sse(raw: str) -> tuple[str | None, dict[str, Any]]:
    event: str | None = None
    data_lines: list[str] = []
    for line in raw.splitlines():
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
    if not data_lines:
        return event, {}
    try:
        return event, json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        return event, {}


async def adapt_internal_stream(
    chunks: AsyncGenerator[str, None],
    *,
    run_id: str,
    thread_id: str | None,
    agent_id: str,
) -> AsyncGenerator[str, None]:
    yield format_sse(
        "run_start",
        {"run_id": run_id, "thread_id": thread_id, "agent_id": agent_id},
    )
    async for raw in chunks:
        event, data = _parse_internal_sse(raw)
        if event == "content_delta":
            yield format_sse(
                "message",
                {"delta": data.get("content") or data.get("delta") or ""},
            )
        elif event in {"tool_call_start", "tool_call_result"}:
            yield format_sse("tool_update", {"kind": event, "data": data})
        elif event == "interrupt":
            yield format_sse("interrupt_blocked", {"data": data})
        elif event == "error":
            yield format_sse("error", {"message": data.get("message") or "Agent run failed"})
    yield format_sse("run_end", {"run_id": run_id, "thread_id": thread_id})


def format_openai_data_sse(payload: dict[str, Any] | str) -> str:
    if isinstance(payload, str):
        data = payload
    else:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"data: {data}\n\n"


def _openai_chunk(
    *,
    run_id: str,
    model: str,
    created_at: datetime,
    delta: dict[str, Any],
    finish_reason: str | None,
) -> dict[str, Any]:
    return {
        "id": run_id,
        "object": "chat.completion.chunk",
        "created": int(created_at.timestamp()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


async def adapt_internal_stream_to_openai(
    chunks: AsyncGenerator[str, None],
    *,
    run_id: str,
    model: str,
    created_at: datetime,
) -> AsyncGenerator[str, None]:
    yield format_openai_data_sse(
        _openai_chunk(
            run_id=run_id,
            model=model,
            created_at=created_at,
            delta={"role": "assistant"},
            finish_reason=None,
        )
    )
    async for raw in chunks:
        event, data = _parse_internal_sse(raw)
        if event == "content_delta":
            delta = data.get("content") or data.get("delta") or ""
            if delta:
                yield format_openai_data_sse(
                    _openai_chunk(
                        run_id=run_id,
                        model=model,
                        created_at=created_at,
                        delta={"content": delta},
                        finish_reason=None,
                    )
                )
        elif event == "error":
            yield format_openai_data_sse(
                {"error": {"message": data.get("message") or "Agent run failed"}}
            )
    yield format_openai_data_sse(
        _openai_chunk(
            run_id=run_id,
            model=model,
            created_at=created_at,
            delta={},
            finish_reason="stop",
        )
    )
    yield format_openai_data_sse("[DONE]")
