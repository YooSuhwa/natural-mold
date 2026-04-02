from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


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
