from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.agent_runtime import event_names
from app.agent_runtime.event_broker import BrokeredEvent, EventBroker
from app.agent_runtime.run_metrics_types import TerminalRunState

_TERMINAL_STATUSES = frozenset({"completed", "canceled", "failed", "interrupted", "stale"})


def _status(event: Mapping[str, object]) -> str | None:
    if event.get("event") == event_names.MESSAGE_END:
        data = event.get("data")
        value = data.get("status") if isinstance(data, Mapping) else None
        return value if value in _TERMINAL_STATUSES else None
    if event.get("method") == "lifecycle":
        data = event.get("data")
        value = data.get("event") if isinstance(data, Mapping) else None
        return value if value in _TERMINAL_STATUSES else None
    data = event.get("data")
    if not isinstance(data, Mapping) or data.get("method") != "lifecycle":
        return None
    params = data.get("params")
    lifecycle = params.get("data") if isinstance(params, Mapping) else None
    value = lifecycle.get("event") if isinstance(lifecycle, Mapping) else None
    return value if value in _TERMINAL_STATUSES else None


def _is_root_terminal(event: Mapping[str, object], run_id: str) -> bool:
    if _status(event) is None:
        return False
    event_run_id = event.get("run_id")
    if isinstance(event_run_id, str):
        namespace = event.get("namespace")
        return event_run_id == run_id and namespace in (None, [])
    data = event.get("data")
    if isinstance(data, Mapping) and data.get("method") == "lifecycle":
        params = data.get("params")
        namespace = params.get("namespace") if isinstance(params, Mapping) else None
        if namespace not in (None, []):
            return False
    event_id = event.get("id")
    return isinstance(event_id, str) and (
        event_id.startswith(f"{run_id}-") or event_id.startswith(f"{run_id}:")
    )


class OwnerTerminalDelivery:
    """Hold the root terminal until its owning worker commits the run decision."""

    def __init__(
        self,
        *,
        run_id: str,
        broker: EventBroker,
        persist_callback: Callable[[list[dict[str, Any]]], Awaitable[None]],
        trace_sink: list[dict[str, Any]],
    ) -> None:
        self.run_id = run_id
        self._broker = broker
        self._persist_callback = persist_callback
        self._trace_sink = trace_sink
        self._live_terminal: BrokeredEvent | None = None
        self._persisted_terminal: dict[str, Any] | None = None

    @property
    def last_event_id(self) -> str | None:
        return self._broker.last_event_id

    async def wait_until_subscribed(self) -> None:
        await self._broker.wait_until_subscribed()

    def publish_nowait(self, event: BrokeredEvent) -> None:
        if _is_root_terminal(event, self.run_id):
            self._live_terminal = event
            return
        self._broker.publish_nowait(event)

    async def persist(self, events: list[dict[str, Any]]) -> None:
        nonterminal: list[dict[str, Any]] = []
        for event in events:
            if _is_root_terminal(event, self.run_id):
                self._persisted_terminal = event
            else:
                nonterminal.append(event)
        if nonterminal:
            await self._persist_callback(nonterminal)

    def close(self, *, error: Exception | None = None) -> None:
        del error

    def remove_staged_terminal_from_trace(self) -> None:
        self._trace_sink[:] = [
            event for event in self._trace_sink if not _is_root_terminal(event, self.run_id)
        ]

    def terminal_events_for_status(
        self,
        status: TerminalRunState,
    ) -> tuple[BrokeredEvent, dict[str, Any]]:
        staged_status = _status(self._persisted_terminal or {})
        if (
            status != "canceled"
            and staged_status == status
            and self._live_terminal is not None
            and self._persisted_terminal is not None
        ):
            return self._live_terminal, self._persisted_terminal
        event_id = f"{self.run_id}-{status}"
        terminal: BrokeredEvent = {
            "id": event_id,
            "event": event_names.MESSAGE_END,
            "data": {"usage": {}, "content": "", "status": status},
        }
        return terminal, dict(terminal)

    def publish_terminal(self, event: BrokeredEvent, persisted_event: dict[str, Any]) -> None:
        self._trace_sink.append(persisted_event)
        self._broker.publish_nowait(event)
