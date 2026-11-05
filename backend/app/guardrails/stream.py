"""⑤ output: guard what streams to the user before it leaves (guardrail-minimum-plan.md §3.1).

``GuardrailStreamTransformer`` mutates LangGraph v3 protocol events in place, before the
built-in transformers snapshot them, so the SSE stream, the stored message events and the
``values`` snapshots carry the checked text. The chat path streams through v3
(``astream_events(version="v3")``); the older ``stream_mode="messages"`` path is not
production and only gets the stored-message check in the middleware.

Streamed text is held back: detection runs on a window once ``LOOKBACK + BURST`` characters
have arrived, everything but the last ``LOOKBACK`` is released, and the tail stays until more
text or the block's finish event. A value split across deltas is therefore seen whole; the
finish event carries the full checked text, which is what clients keep. Detection (the
model pack is a network call) runs about once per ``BURST`` characters, not per token.
"""

from __future__ import annotations

from typing import Any, ClassVar

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langgraph.stream import StreamTransformer

from app.guardrails.actions import block_notice
from app.guardrails.rules import Stage

LOOKBACK = 128
BURST = 128


class StreamRedactor:
    """Held-back checking of streamed strings, one buffer per (key, field)."""

    def __init__(self, guard: Any) -> None:
        self.guard = guard
        self._held: dict[tuple[Any, ...], str] = {}
        self._blocked: set[tuple[Any, ...]] = set()

    async def check(self, text: str, stage: Stage) -> str:
        """The whole text as it may be shown; a block becomes the notice."""
        if not text:
            return text
        [applied] = await self.guard.check([text], stage)
        return block_notice(applied.decisions) if applied.blocked else applied.text

    async def feed(self, key: tuple[Any, ...], text: str, stage: Stage) -> str:
        if key in self._blocked:
            return ""
        held = self._held.get(key, "") + text
        if len(held) < LOOKBACK + BURST:
            self._held[key] = held
            return ""
        [applied] = await self.guard.check([held], stage)
        if applied.blocked:
            self._blocked.add(key)
            self._held.pop(key, None)
            return block_notice(applied.decisions)
        cut = len(applied.text) - LOOKBACK
        self._held[key] = applied.text[cut:]
        return applied.text[:cut]

    def drop(self, key: tuple[Any, ...]) -> None:
        self._held.pop(key, None)

    def drop_prefix(self, prefix: Any) -> None:
        for key in [k for k in self._held if k[0] == prefix]:
            del self._held[key]
        self._blocked = {k for k in self._blocked if k[0] != prefix}

    async def message(self, message: BaseMessage) -> BaseMessage:
        """A copy of a finished message as it may be shown (AI: output; tool: tool_result)."""
        if isinstance(message, AIMessage):
            stage: Stage = "output"
        elif isinstance(message, ToolMessage):
            stage = "tool_result"
        else:
            return message  # the user's own input
        content = message.content
        if isinstance(content, str):
            checked = await self.check(content, stage)
            return (
                message if checked == content else message.model_copy(update={"content": checked})
            )
        if isinstance(content, list):
            blocks: list[Any] = []
            changed = False
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                    text = await self.check(block["text"], stage)
                    if text != block["text"]:
                        block, changed = {**block, "text": text}, True
                blocks.append(block)
            return message.model_copy(update={"content": blocks}) if changed else message
        return message

    async def value(self, value: Any) -> Any:
        if isinstance(value, BaseMessage):
            return await self.message(value)
        if isinstance(value, dict):
            return {k: await self.value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [await self.value(v) for v in value]
        if isinstance(value, tuple):
            return tuple([await self.value(v) for v in value])
        return value

    async def strings(self, value: Any, stage: Stage) -> Any:
        """Every string leaf of a structure (tool input/output on the ``tools`` channel)."""
        if isinstance(value, str):
            return await self.check(value, stage)
        if isinstance(value, BaseMessage):
            return await self.message(value)
        if isinstance(value, dict):
            return {k: await self.strings(v, stage) for k, v in value.items()}
        if isinstance(value, list):
            return [await self.strings(v, stage) for v in value]
        return value


_DELTA_FIELDS: dict[str, tuple[str, Stage]] = {
    "text-delta": ("text", "output"),
    "reasoning-delta": ("reasoning", "output"),
    "tool-call-delta": ("args", "tool_args"),
}
_FINISH_FIELDS: dict[str, tuple[str, Stage]] = {
    "text": ("text", "output"),
    "reasoning": ("reasoning", "output"),
}


class GuardrailStreamTransformer(StreamTransformer):
    """Checks ``messages``, ``tools`` and ``values`` events before any built-in sees them."""

    before_builtins: ClassVar[bool] = True
    required_stream_modes: ClassVar[tuple[str, ...]] = ("messages", "tools", "values")

    def __init__(self, scope: tuple[str, ...] = (), *, guard: Any) -> None:
        super().__init__(scope)
        self.redactor = StreamRedactor(guard)

    def init(self) -> dict[str, Any]:
        return {}  # mutates events in place; no projection

    def process(self, event: Any) -> bool:  # pragma: no cover - the mux uses aprocess
        raise RuntimeError("guardrails run on the async agent API only")

    async def aprocess(self, event: Any) -> bool:
        method = event.get("method")
        params = event.get("params") or {}
        if method == "messages":
            await self._messages(params)
        elif method == "tools":
            await self._tools(params.get("data"))
        elif method == "values" and params.get("data") is not None:
            params["data"] = await self.redactor.value(params["data"])
        return True

    async def _messages(self, params: dict[str, Any]) -> None:
        data = params.get("data")
        if not isinstance(data, tuple) or len(data) != 2:  # noqa: PLR2004
            return
        payload, metadata = data
        if isinstance(payload, BaseMessage):  # models without token streaming
            params["data"] = (await self.redactor.message(payload), metadata)
            return
        if not isinstance(payload, dict):
            return
        run_id = str((metadata or {}).get("run_id") or "")
        kind = payload.get("event")
        index = payload.get("index")
        if kind == "content-block-delta" and isinstance(index, int):
            delta = payload.get("delta")
            if isinstance(delta, dict) and delta.get("type") in _DELTA_FIELDS:
                field, stage = _DELTA_FIELDS[delta["type"]]
                text = delta.get(field)
                if isinstance(text, str) and text:
                    delta[field] = await self.redactor.feed((run_id, index, field), text, stage)
        elif kind == "content-block-finish" and isinstance(index, int):
            content = payload.get("content")
            if isinstance(content, dict):
                await self._finish(content, run_id, index)
        elif kind in {"message-finish", "error"}:
            self.redactor.drop_prefix(run_id)

    async def _finish(self, content: dict[str, Any], run_id: str, index: int) -> None:
        kind = content.get("type")
        if kind in _FINISH_FIELDS:
            field, stage = _FINISH_FIELDS[kind]
            text = content.get(field)
            if isinstance(text, str):
                content[field] = await self.redactor.check(text, stage)
            self.redactor.drop((run_id, index, field))
        elif kind in {"tool_call", "server_tool_call", "invalid_tool_call"}:
            if content.get("args") is not None:
                content["args"] = await self.redactor.strings(content["args"], "tool_args")
            self.redactor.drop((run_id, index, "args"))

    async def _tools(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        kind = data.get("event")
        call_id = data.get("tool_call_id") or ""
        if kind == "tool-started" and "input" in data:
            data["input"] = await self.redactor.strings(data["input"], "tool_args")
        elif kind == "tool-output-delta":
            delta = data.get("delta")
            if isinstance(delta, str) and delta:
                data["delta"] = await self.redactor.feed(("tool", call_id), delta, "tool_result")
            elif isinstance(delta, dict | list):
                data["delta"] = await self.redactor.strings(delta, "tool_result")
        elif kind == "tool-finished":
            if "output" in data:
                data["output"] = await self.redactor.strings(data["output"], "tool_result")
            self.redactor.drop(("tool", call_id))
        elif kind == "tool-error":
            if isinstance(data.get("message"), str):
                data["message"] = await self.redactor.check(data["message"], "tool_result")
            self.redactor.drop(("tool", call_id))
