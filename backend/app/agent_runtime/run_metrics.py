"""Pure, replay-safe metrics aggregation for one conversation run.

Integration API:

1. Construct after the run is claimed with the same injected monotonic start
   mark used by the worker and the pre-input checkpoint's known assistant IDs:
   ``RunMetricsAccumulator(started_at=time.monotonic(),
   baseline_message_identities=...)``.
2. Call :meth:`observe` for every adapted ``StoredProtocolEvent`` and every
   synthesized usage/tool event before persistence/replay fan-out.
3. Bracket every actual model invocation from the runtime with
   :meth:`start_model_generation` and :meth:`finish_model_generation`. Stored
   protocol events alone do not expose a trustworthy model-start boundary, so
   generation duration/TPS remain unavailable until the integration provides it.
4. In the worker's terminal ``finally`` path (including ``CancelledError``),
   call :meth:`finalize` and persist ``snapshot.to_persistence_payload()``.

Usage is keyed by ``(namespace, assistant message id)``. Each payload is a
cumulative provider snapshot, so only its increase from the last snapshot is
added. Tool and subagent identities are ``(namespace, call id)``; an empty
namespace is root and every non-empty namespace is descendant. A ``task`` tool
call is a tool call and also a subagent call; the corresponding ``tasks`` /
``subagents`` discovery event reuses the same subagent identity and therefore
does not inflate the subagent count. Events without a stable identity leave the
affected count unknown rather than risking an inflated total.
Activity records one entry per model message and retain only the bounded newest
tail; ``activity_truncated`` tells persistence/UI that older activity was removed.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection

from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.agent_runtime.protocol_usage_normalization import (
    UsageCandidate,
    has_content,
    is_model_message,
    merge_usage_candidate,
    protocol_message_mappings,
    text_value,
    usage_candidate_from_mapping,
    usage_mapping,
)

__all__ = [
    "MessageIdentity",
    "RunMetricActivity",
    "RunMetricsAccumulator",
    "RunMetricsPayload",
    "RunMetricsSnapshot",
    "TerminalRunState",
    "event_has_model_content",
]
from app.agent_runtime.run_metrics_activity import RunActivityAccumulator
from app.agent_runtime.run_metrics_types import (
    MessageIdentity,
    RunMetricActivity,
    RunMetricsPayload,
    RunMetricsSnapshot,
    TerminalRunState,
)


def event_has_model_content(event: StoredProtocolEvent) -> bool:
    """Return whether a normalized event carries a non-empty model content chunk."""
    return any(
        has_content(message) and is_model_message(message)
        for message in protocol_message_mappings(event)
    )


class RunMetricsAccumulator:
    """Mutable accumulator because event replay incrementally advances one run's state."""

    def __init__(
        self,
        *,
        started_at: float | None,
        monotonic: Callable[[], float] = time.monotonic,
        complete_event_capture: bool = True,
        observe_protocol_events: bool = True,
        baseline_message_identities: Collection[MessageIdentity] = (),
        activity_limit: int = 100,
    ) -> None:
        self._started_at = started_at
        self._monotonic = monotonic
        self._complete_event_capture = complete_event_capture
        self._observe_protocol_events = observe_protocol_events
        self._baseline_message_identities = set(baseline_message_identities)
        self._terminal_state: TerminalRunState | None = None
        self._terminal_snapshot: RunMetricsSnapshot | None = None
        self._first_token_at: float | None = None
        self._model_generation_started_at: dict[str, float] = {}
        self._model_generation_ms = 0.0
        self._has_model_generation_interval = False
        self._usage_by_message: dict[MessageIdentity, UsageCandidate] = {}
        self._pending_model_messages: set[MessageIdentity] = set()
        self._activities = RunActivityAccumulator(
            complete_event_capture=complete_event_capture,
            activity_limit=activity_limit,
        )

    def observe(self, event: StoredProtocolEvent) -> None:
        """Consume one post-adapter protocol event; replays are idempotent by stable IDs."""
        if self._terminal_snapshot is not None:
            return
        if not self._observe_protocol_events:
            return
        namespace = tuple(event["namespace"])
        self._observe_model_messages(event, namespace)
        self._activities.observe(event, elapsed_ms=self._elapsed_ms(self._monotonic()))

    def start_model_generation(self, invocation_id: str = "legacy") -> None:
        """Mark a real model invocation start from the stream lifecycle integration."""
        if (
            self._terminal_snapshot is not None
            or invocation_id in self._model_generation_started_at
        ):
            return
        self._model_generation_started_at[invocation_id] = self._monotonic()
        self._has_model_generation_interval = True

    def finish_model_generation(self, invocation_id: str = "legacy") -> None:
        """Close the active model invocation interval at its real lifecycle boundary."""
        if self._terminal_snapshot is not None:
            return
        self._finish_model_generation(invocation_id, self._monotonic())

    def finalize(self, terminal_state: TerminalRunState) -> RunMetricsSnapshot:
        """Freeze the terminal state while retaining all partial metrics gathered so far."""
        if self._terminal_snapshot is not None:
            return self._terminal_snapshot
        finished_at = self._monotonic()
        for invocation_id in tuple(self._model_generation_started_at):
            self._finish_model_generation(invocation_id, finished_at)
        self._terminal_state = terminal_state
        self._terminal_snapshot = self._build_snapshot(finished_at)
        return self._terminal_snapshot

    def snapshot(self) -> RunMetricsSnapshot:
        """Return current measurements without fabricating unavailable provider data."""
        if self._terminal_snapshot is not None:
            return self._terminal_snapshot
        return self._build_snapshot(self._monotonic())

    def _build_snapshot(self, now: float) -> RunMetricsSnapshot:
        elapsed_ms = self._elapsed_ms(now)
        token_totals = self._token_totals()
        ttft_ms, generation_ms, tokens_per_second = self._timing(now, token_totals)
        return RunMetricsSnapshot(
            terminal_state=self._terminal_state,
            elapsed_ms=elapsed_ms,
            ttft_ms=ttft_ms,
            generation_ms=generation_ms,
            tokens_per_second=tokens_per_second,
            prompt_tokens=token_totals[0],
            completion_tokens=token_totals[1],
            cache_creation_tokens=token_totals[2],
            cache_read_tokens=token_totals[3],
            estimated_cost=token_totals[4],
            usage_complete=self._usage_complete(),
            root_tool_calls=self._activities.root_tool_calls,
            descendant_tool_calls=self._activities.descendant_tool_calls,
            root_subagent_calls=self._activities.root_subagent_calls,
            descendant_subagent_calls=self._activities.descendant_subagent_calls,
            activity=self._activities.activity,
            activity_truncated=self._activities.activity_truncated,
        )

    def _observe_model_messages(
        self,
        event: StoredProtocolEvent,
        namespace: tuple[str, ...],
    ) -> None:
        for message in protocol_message_mappings(event):
            message_id = text_value(message.get("id") or message.get("assistant_msg_id"))
            usage = usage_mapping(message)
            possible_identity = (namespace, message_id) if message_id is not None else None
            if (
                possible_identity is not None
                and possible_identity in self._baseline_message_identities
            ):
                continue
            if (
                self._first_token_at is None
                and has_content(message)
                and (is_model_message(message) or usage is not None)
            ):
                self._first_token_at = self._monotonic()
            if message_id is None:
                continue
            identity: MessageIdentity = (namespace, message_id)
            if usage is None:
                if is_model_message(message):
                    self._pending_model_messages.add(identity)
                continue
            self._pending_model_messages.discard(identity)
            snapshot = usage_candidate_from_mapping(message)
            if snapshot is None:
                if is_model_message(message):
                    self._pending_model_messages.add(identity)
                continue
            prior = self._usage_by_message.get(identity)
            merged = merge_usage_candidate(prior, snapshot)
            if prior == merged:
                continue
            self._usage_by_message[identity] = merged
            self._activities.record_model_usage(
                identity,
                elapsed_ms=self._elapsed_ms(self._monotonic()),
            )

    def _finish_model_generation(self, invocation_id: str, finished_at: float) -> None:
        started_at = self._model_generation_started_at.pop(invocation_id, None)
        if started_at is None:
            return
        self._model_generation_ms += max(0.0, (finished_at - started_at) * 1000)

    def _elapsed_ms(self, now: float) -> float | None:
        if self._started_at is None:
            return None
        return round((now - self._started_at) * 1000, 1)

    def _token_totals(self) -> tuple[int | None, int | None, int | None, int | None, float | None]:
        if not self._usage_by_message:
            return None, None, None, None, None
        usage = tuple(self._usage_by_message.values())
        if any(item["estimated_cost"] is None for item in usage):
            estimated_cost: float | None = None
        else:
            estimated_cost = round(sum(item["estimated_cost"] or 0.0 for item in usage), 8)
        return (
            sum(item["prompt_tokens"] for item in usage),
            sum(item["completion_tokens"] for item in usage),
            sum(item["cache_creation_tokens"] for item in usage),
            sum(item["cache_read_tokens"] for item in usage),
            estimated_cost,
        )

    def _usage_complete(self) -> bool:
        if not self._complete_event_capture or self._pending_model_messages:
            return False
        return bool(self._usage_by_message) or not self._has_model_generation_interval

    def _timing(
        self,
        now: float,
        token_totals: tuple[int | None, int | None, int | None, int | None, float | None],
    ) -> tuple[float | None, float | None, float | None]:
        if self._started_at is None:
            return None, None, None
        ttft_ms = (
            round((self._first_token_at - self._started_at) * 1000, 1)
            if self._first_token_at is not None
            else None
        )
        if not self._has_model_generation_interval:
            return ttft_ms, None, None
        active_generation_ms = self._model_generation_ms
        active_generation_ms += sum(
            max(0.0, (now - started_at) * 1000)
            for started_at in self._model_generation_started_at.values()
        )
        generation_ms = round(active_generation_ms, 1)
        completion_tokens = token_totals[1]
        if completion_tokens is None or generation_ms <= 0:
            return ttft_ms, generation_ms, None
        return ttft_ms, generation_ms, round(completion_tokens / (generation_ms / 1000), 1)
