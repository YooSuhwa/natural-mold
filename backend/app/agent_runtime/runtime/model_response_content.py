"""Content and tool-call detection shared by chat retry policies."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ModelResponse
from langchain_core.messages import AIMessage


def has_visible_ai_content(response: ModelResponse[Any] | AIMessage) -> bool:
    messages = [response] if isinstance(response, AIMessage) else list(response.result)
    for message in messages:
        if getattr(message, "type", None) != "ai":
            continue
        if getattr(message, "tool_calls", None):
            return True
        content = getattr(message, "content", None)
        if isinstance(content, str):
            if content.strip():
                return True
            continue
        if isinstance(content, list):
            for block in content:
                if isinstance(block, str) and block.strip():
                    return True
                if isinstance(block, dict) and str(block.get("text") or "").strip():
                    return True
    return False
