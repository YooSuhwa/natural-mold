"""Project the model's public messages and tools into one AG-UI run."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ag_ui.core import BaseEvent, EventType

from app.agent_runtime.ag_ui_v1_calls import PublicToolCalls
from app.agent_runtime.ag_ui_v1_text import PublicTextMessages, message_id
from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.marketplace.redaction import prepare_secret_values


class PublicChatEntities:
    def __init__(
        self,
        run_id: str,
        allow_tool: Callable[[str | None, str], bool],
        *,
        secret_values: Iterable[str] = (),
    ) -> None:
        secrets = prepare_secret_values(secret_values)
        self._messages = PublicTextMessages(run_id, secrets)
        self._calls = PublicToolCalls(run_id, allow_tool, secret_values=secrets)
        self._non_assistant_streams: set[str | None] = set()
        self._non_assistant_ids: set[tuple[str | None, str]] = set()

    def known_message_id(self, owner: str | None, raw_id: str | None) -> str | None:
        return self._messages.known(owner, raw_id)

    def current_message_id(self, owner: str | None) -> str | None:
        return self.known_message_id(owner, self._messages.current.get(owner))

    def observed_message_id(self, owner: str | None, raw_id: str | None) -> str | None:
        return self._messages.observed(owner, raw_id)

    def current_observed_message_id(self, owner: str | None) -> str | None:
        return self.observed_message_id(owner, self._messages.current.get(owner))

    def public_tool_id(self, owner: str | None, raw_id: str | None) -> str | None:
        return self._calls.public_id(owner, raw_id)

    def tool_parent_message_id(self, owner: str | None, raw_id: str | None) -> str | None:
        return self._calls.parent_id(owner, raw_id)

    def project(self, event: StoredProtocolEvent, *, owner: str | None) -> list[BaseEvent]:
        data = event["data"]
        if isinstance(data, (list, tuple)) and len(data) == 2:
            data = data[0]  # Historical (message, metadata) records.
        if not isinstance(data, Mapping):
            return []
        if event["method"] == "tools":
            return self._calls.tool(data, owner=owner, event_id=event["id"])
        if event["method"] != "messages":
            return []
        return self._project_message(data, owner, event["id"])

    def _project_message(
        self, data: Mapping[str, Any], owner: str | None, event_id: str
    ) -> list[BaseEvent]:
        lifecycle = data.get("event")
        if lifecycle is not None and not isinstance(lifecycle, str):
            return []
        kind = str(data.get("role") or data.get("type") or "").lower()
        raw_id = message_id(data)
        assistant = kind in {"ai", "assistant", "aimessage", "aimessagechunk"}
        if lifecycle == "message-start" and kind and not assistant:
            if raw_id is not None:
                self._non_assistant_ids.add((owner, raw_id))
            self._non_assistant_streams.add(owner)
            self._messages.clear_current(owner)
            return []
        if raw_id is not None and (owner, raw_id) in self._non_assistant_ids:
            return []
        if lifecycle == "message-start" or (lifecycle is None and assistant):
            self._non_assistant_streams.discard(owner)
        if (
            lifecycle is not None
            and owner in self._non_assistant_streams
            and self.observed_message_id(owner, raw_id) is None
        ):
            return []
        if kind in {"tool", "toolmessage", "toolmessagechunk"}:
            # The source message is the tool's reply, never an issuing assistant.
            return self._calls.message(data, owner=owner, parent_id="", event_id=event_id)
        if kind and not assistant:
            return []
        msg = self._messages.select(owner, raw_id, event_id, starts=lifecycle == "message-start")
        if assistant and lifecycle in {None, "message-start"}:
            msg.observed = True
        frames = self._messages.project(data, owner, msg)
        tools = self._calls.message(data, owner=owner, parent_id=msg.public_id, event_id=event_id)
        if any(frame.type == EventType.TOOL_CALL_START for frame in tools):
            msg.visible = True
        frames.extend(tools)
        if lifecycle is None and kind in {"ai", "assistant", "aimessage"}:
            frames.extend(self._messages.finish(msg, owner))
        return frames

    def close(self, owner: str | None) -> list[BaseEvent]:
        return [*self._messages.close(owner), *self._calls.close(owner)]
