from __future__ import annotations

import asyncio
import logging
import time

from app.agent_runtime.event_broker import BrokeredEvent, EventBroker
from app.agent_runtime.journal_continuity import trim_journal_retry_buffer
from app.agent_runtime.langgraph_event_projection import (
    annotate_session_consent_eligibility,
    project_stable_event,
)
from app.agent_runtime.protocol_events import (
    StoredProtocolEvent,
    canonical_input_requested_events,
    format_protocol_sse,
    protocol_event_cursor,
    to_protocol_wire_event,
)
from app.agent_runtime.recovery_stream import RecoveryStreamCursor, state_output_proof
from app.agent_runtime.streaming import PersistCallback
from app.chat_recovery.fence import current_run_fence

logger = logging.getLogger(__name__)
_FLUSH_BATCH_SIZE = 32
_FLUSH_INTERVAL_SECONDS = 2.0
_MAX_RETRY_BUFFER_EVENTS = 5000


def _broker_event(event: StoredProtocolEvent) -> BrokeredEvent:
    return {
        "id": protocol_event_cursor(event),
        "event": "message",
        "data": dict(to_protocol_wire_event(event)),
    }


class ProtocolEventDelivery:
    """Mutable run-scoped delivery state for sequencing, persistence, and broker egress."""

    def __init__(
        self,
        *,
        run_id: str,
        trace_sink: list[dict[str, object]] | None = None,
        broker: EventBroker | None = None,
        persist_callback: PersistCallback | None = None,
        session_consent_tools: list[str] | None = None,
        continuation: RecoveryStreamCursor | None = None,
    ) -> None:
        self.run_id = run_id
        self.trace_sink = trace_sink
        self.broker = broker
        self.persist_callback = persist_callback
        self.session_consent_tools = session_consent_tools
        self.max_emitted_seq = continuation.last_seq if continuation else -1
        self._cursor_ids = set(continuation.event_ids) if continuation else None
        self._durable_callback = None
        if current_run_fence() is not None:
            if persist_callback is None:
                raise RuntimeError("Recoverable chat requires durable event delivery")
            self._durable_callback = persist_callback
            if self._cursor_ids is None:
                self._cursor_ids = set()
        self.input_requested_emitted = False
        self.emitted: list[StoredProtocolEvent] = []
        self._persist_buffer: list[dict[str, object]] = []
        self._last_persist_flush_at = time.monotonic()
        self._background_persist_tasks: set[asyncio.Task[None]] = set()

    async def _persist_chunk(self, events: list[dict[str, object]]) -> None:
        if self.persist_callback is None:
            return
        try:
            await self.persist_callback(events)
        except Exception:  # noqa: BLE001 - persistence failure must not kill a live stream.
            self._persist_buffer = [*events, *self._persist_buffer]
            logger.exception("protocol stream persist_callback failed (run_id=%s)", self.run_id)
            overflow = trim_journal_retry_buffer(self._persist_buffer, _MAX_RETRY_BUFFER_EVENTS)
            if overflow:
                logger.warning(
                    "persist buffer overflow (run_id=%s) - dropped %d oldest events at cap=%d",
                    self.run_id,
                    overflow,
                    _MAX_RETRY_BUFFER_EVENTS,
                )

    def _schedule_persist_flush(self) -> None:
        if (
            self.persist_callback is None
            or not self._persist_buffer
            or self._background_persist_tasks
        ):
            return
        elapsed = time.monotonic() - self._last_persist_flush_at
        if len(self._persist_buffer) < _FLUSH_BATCH_SIZE and elapsed < _FLUSH_INTERVAL_SECONDS:
            return
        events = self._persist_buffer
        self._persist_buffer = []
        self._last_persist_flush_at = time.monotonic()
        task = asyncio.create_task(self._persist_chunk(events))
        self._background_persist_tasks.add(task)
        task.add_done_callback(self._background_persist_tasks.discard)

    async def emit(self, event: StoredProtocolEvent) -> str:
        projected = project_stable_event(event, next_seq=self.max_emitted_seq + 1)
        wire_event: StoredProtocolEvent = projected.wire_event
        persistable = dict(projected.persistable_event)
        if self._cursor_ids is not None:
            # Upstream stream IDs (including fallback seq-based IDs) can start
            # again at zero after a process restart. A reused cursor would
            # cause storage and reconnect clients to silently lose a new event.
            cursor = protocol_event_cursor(wire_event)
            if cursor in self._cursor_ids:
                cursor = f"{self.run_id}:continued:{wire_event['seq']}"
                wire_event = {**wire_event, "id": cursor, "upstream_event_id": cursor}
                persistable.update(id=cursor, upstream_event_id=cursor)
            self._cursor_ids.add(cursor)
        if self._durable_callback is not None:
            if wire_event["method"] in {"values", "updates"}:
                call_ids: set[str] = set()
                data = wire_event["data"]
                if not isinstance(data, dict) or state_output_proof(data, call_ids):
                    persistable["recovery_output_started"] = True
                if call_ids:
                    persistable["recovery_tool_call_ids"] = sorted(call_ids)
            await self._durable_callback([persistable])
        else:
            self._persist_buffer.append(persistable)
        self.max_emitted_seq = wire_event["seq"]
        if wire_event["method"] == "input.requested":
            self.input_requested_emitted = True

        self.emitted.append(wire_event)
        if self.trace_sink is not None:
            self.trace_sink.append(persistable)
        if self.broker is not None:
            self.broker.publish_nowait(_broker_event(wire_event))
        self._schedule_persist_flush()
        return format_protocol_sse(wire_event)

    async def emit_canonical_interrupts(self, event: StoredProtocolEvent) -> list[str]:
        chunks: list[str] = []
        for input_event in canonical_input_requested_events(
            event,
            first_seq=self.max_emitted_seq + 1,
        ):
            if self.session_consent_tools:
                annotate_session_consent_eligibility(
                    input_event,
                    self.session_consent_tools,
                )
            chunks.append(await self.emit(input_event))
        return chunks

    async def close(self) -> None:
        """Join partial flushes, retry the remainder once, then close the broker."""

        if self._background_persist_tasks:
            await asyncio.gather(*self._background_persist_tasks, return_exceptions=True)
        if self.persist_callback is not None and self._persist_buffer:
            final_events = self._persist_buffer
            self._persist_buffer = []
            try:
                await self.persist_callback(final_events)
            except Exception:  # noqa: BLE001 - final loss is logged after the bounded retry.
                logger.exception(
                    "final flush persist_callback failed (run_id=%s) - %d events lost",
                    self.run_id,
                    len(final_events),
                )
        if self.broker is not None:
            self.broker.close()
