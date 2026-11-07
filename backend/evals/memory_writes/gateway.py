"""Company LiteLLM gateway calls for the write eval, cached in one JSON file.

Reuses the recall eval's transport (``LITELLM_BASE_URL`` ends in ``/v1``; the key is never
printed or cached). Each reply is keyed by the hash of its request, so a re-run costs nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.memory_recall.gateway import _hash, _post

CACHE = Path(__file__).parent / "cache" / "llm.json"
calls = {"chat": 0}
_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is None:
        loaded: dict[str, Any] = json.loads(CACHE.read_text()) if CACHE.exists() else {}
        _cache = loaded
        return loaded
    return _cache


def _save() -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(_load(), ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def complete(
    model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The reply message (``content`` and ``tool_calls``), from the cache when present."""
    body: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        body["tools"] = tools
    key = _hash(json.dumps(body, ensure_ascii=False, sort_keys=True))
    cache = _load()
    if key not in cache:
        calls["chat"] += 1
        reply = _post("/chat/completions", body)
        message = reply["choices"][0]["message"]
        usage = reply.get("usage") or {}
        cache[key] = {
            "model": model,
            "content": message.get("content"),
            "tool_calls": [
                {"name": c["function"]["name"], "arguments": c["function"]["arguments"]}
                for c in message.get("tool_calls") or []
            ],
            "tokens": [usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)],
        }
        _save()
    return cache[key]


def text(model: str, prompt: str, system: str | None = None) -> str:
    messages = [{"role": "system", "content": system}] if system else []
    messages.append({"role": "user", "content": prompt})
    return complete(model, messages)["content"] or ""
