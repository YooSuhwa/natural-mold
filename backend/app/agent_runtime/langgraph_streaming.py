from __future__ import annotations

import time
import uuid
from collections.abc import AsyncGenerator
from functools import partial
from typing import Any

from langgraph.types import Command

from app.agent_runtime import langgraph_event_projection as projection
from app.agent_runtime import langgraph_stream_ingestion
from app.agent_runtime.event_broker import EventBroker
from app.agent_runtime.langgraph_compaction_start import SummaryStart, with_compaction_starts
from app.agent_runtime.langgraph_event_delivery import ProtocolEventDelivery
from app.agent_runtime.langgraph_lifecycle_events import (
    error_protocol_event,
    lifecycle_protocol_event,
    terminal_lifecycle_event,
)
from app.agent_runtime.langgraph_message_identity import collect_root_ai_message_id
from app.agent_runtime.langgraph_pending_inputs import pending_input_requested_events
from app.agent_runtime.langgraph_tool_event_synthesis import synthesize_tool_events_from_values
from app.agent_runtime.offload_protocol_projection import project_offload_egress_data
from app.agent_runtime.protocol_events import StoredProtocolEvent
from app.agent_runtime.protocol_side_effects import (
    collect_protocol_side_effect_events,
    prepare_artifact_recorder,
)
from app.agent_runtime.protocol_usage import collect_protocol_usage_event
from app.agent_runtime.recovery_stream import (
    current_turn_messages,
    recovery_stream_cursor,
    state_fingerprint,
    tool_event_key,
)
from app.agent_runtime.run_metrics import RunMetricsAccumulator, event_has_model_content
from app.agent_runtime.run_start_events import head_event_specs
from app.agent_runtime.stream_error_messages import public_stream_error_message
from app.agent_runtime.streaming import (
    ArtifactEventRecorder,
    PersistCallback,
    StreamErrorRecord,
)
from app.chat_recovery.fence import current_run_fence
from app.config import settings
from app.models.conversation_run import RUN_TERMINAL_STATUSES
from app.services.conversation_run_steer_events import RunSteerReceiptFeed, uses_steer_channel

_compaction_history_id = projection.compaction_history_id
_annotate_session_consent_eligibility = projection.annotate_session_consent_eligibility


def _project_offload_egress_data(data: Any) -> Any:
    return project_offload_egress_data(data)


async def stream_agent_response_langgraph(
    agent: Any,
    input_: list[Any] | Command | dict[str, Any] | None,
    config: dict[str, Any],
    *,
    trace_sink: list[dict[str, Any]] | None = None,
    cost_per_input_token: float | None = None,
    cost_per_output_token: float | None = None,
    usage_sink: dict[str, Any] | None = None,
    msg_id_sink: list[str] | None = None,
    error_sink: list[StreamErrorRecord] | None = None,
    broker: EventBroker | None = None,
    persist_callback: PersistCallback | None = None,
    run_id: str | None = None,
    artifact_recorder: ArtifactEventRecorder | None = None,
    subagent_display_names: dict[str, str] | None = None,
    recalled_memories: list[dict[str, Any]] | None = None,
    skill_draft_brief: dict[str, Any] | None = None,
    excluded_capabilities: list[dict[str, str]] | None = None,
    session_consent_tools: list[str] | None = None,
    run_metrics: RunMetricsAccumulator | None = None,
    steer_receipts: RunSteerReceiptFeed | None = None,
) -> AsyncGenerator[str, None]:
    msg_id = run_id or str(uuid.uuid4())
    thread_id = langgraph_stream_ingestion.thread_id_from_config(config, msg_id)
    continuation = recovery_stream_cursor(msg_id, thread_id)
    delivery = ProtocolEventDelivery(
        continuation=continuation,
        run_id=msg_id,
        trace_sink=trace_sink,
        broker=broker,
        persist_callback=persist_callback,
        session_consent_tools=session_consent_tools,
    )
    side_effect_seq = 0
    deferred_empty_input_requested: StoredProtocolEvent | None = None
    seen_usage_keys = set(continuation.usage_keys) if continuation else set()
    seen_synthesized_tool_call_ids: set[str] = set()
    # Native and values-synthesized tool events describe the same transition.
    # Both can occur, and either one can be the last event before a crash.
    dedupe_tool_events = continuation is not None or current_run_fence() is not None
    durable_phases: set[tuple[tuple[str, ...], str, str]] = set()
    if continuation:
        durable_phases.update(((), phase, call_id) for phase, call_id in continuation.tool_phases)
    replaying_values = continuation is not None
    compaction_namespace: tuple[str, ...] | None = None
    compaction_done_emitted = False
    previous_cutoffs: dict[tuple[str, ...], tuple[int, str | None] | None] = {}
    stream_started_at = time.monotonic()
    first_token_at: float | None = None
    artifact_recorder = await prepare_artifact_recorder(artifact_recorder, run_id=msg_id)

    def tool_phase_already_sent(event: StoredProtocolEvent) -> bool:
        key = tool_event_key(event)
        if not dedupe_tool_events or key is None:
            return False
        phase = (tuple(event["namespace"]), *key)
        if phase in durable_phases:
            return True
        durable_phases.add(phase)
        return False

    async def emit(event: StoredProtocolEvent) -> str:
        if run_metrics is not None:
            run_metrics.observe(event)
        return await delivery.emit(event)

    async def emit_head_event(name: str, payload: dict[str, Any], suffix: str) -> str:
        nonlocal side_effect_seq
        side_effect_seq += 1
        return await emit(
            projection.head_custom_event(
                run_id=msg_id,
                thread_id=thread_id,
                seq=side_effect_seq,
                name=name,
                payload=payload,
                stable_suffix=suffix,
            )
        )

    events = with_compaction_starts(
        partial(
            langgraph_stream_ingestion.iter_ingested_protocol_events,
            agent,
            langgraph_stream_ingestion.actual_input(input_),
            run_id=msg_id,
            thread_id=thread_id,
        ),
        config,
        enabled=settings.compaction_marker_enabled,
    )
    try:
        started_event = lifecycle_protocol_event(
            run_id=msg_id, thread_id=thread_id, seq=0, event="running"
        )
        if continuation is None or started_event["id"] not in continuation.event_ids:
            yield await emit(started_event)
        for name, payload, suffix in head_event_specs(
            subagent_display_names=subagent_display_names,
            recalled_memories=recalled_memories,
            skill_draft_brief=skill_draft_brief,
            excluded_capabilities=excluded_capabilities,
        ):
            if continuation is None or f"{msg_id}:{suffix}" not in continuation.event_ids:
                yield await emit_head_event(name, payload, suffix)

        async for ingested in events:
            if isinstance(ingested, SummaryStart) or projection.is_summary_token_event(
                ingested.event
            ):
                if settings.compaction_marker_enabled and compaction_namespace is None:
                    compaction_namespace = (
                        ingested.namespace
                        if isinstance(ingested, SummaryStart)
                        else tuple(ingested.event["namespace"])
                    )
                    side_effect_seq += 1
                    yield await emit(
                        projection.compaction_event(
                            run_id=msg_id, thread_id=thread_id, seq=side_effect_seq, state="running"
                        )
                    )
                continue

            event = ingested.event
            method, data = event["method"], event["data"]
            if continuation is not None and continuation.already_published_steer_message(event):
                continue
            if uses_steer_channel(event):
                # Only committed server receipts may use this channel, including in fallback mode.
                continue
            if steer_receipts is not None and not event["namespace"]:
                terminal = (
                    method == "lifecycle"
                    and isinstance(data, dict)
                    and data.get("event") in RUN_TERMINAL_STATUSES
                )
                if terminal or method in {"values", "updates"}:
                    for receipt in await steer_receipts.changed_events(
                        first_seq=delivery.max_emitted_seq + 1, force=terminal
                    ):
                        if continuation is None or receipt["id"] not in continuation.event_ids:
                            yield await emit(receipt)
                if terminal:
                    steer_receipts = None
            if tool_phase_already_sent(event):
                continue
            repeated_values = False
            if continuation and replaying_values and method == "values" and not event["namespace"]:
                # The first root state replay carries the checkpoint messages.
                # Suppress an identical durable snapshot, but still synthesize
                # a tool phase that had not yet reached the client.
                replaying_values = False
                compact = projection.project_stable_event(event, next_seq=0).persistable_event
                repeated_values = state_fingerprint(compact["data"]) == continuation.last_values
            is_empty_v3_interrupt = (
                ingested.source == "v3" and projection.is_empty_input_requested_event(event)
            )
            if is_empty_v3_interrupt:
                deferred_empty_input_requested = event
                continue

            if settings.compaction_marker_enabled and event["method"] == "values":
                # The first values frame is the restored checkpoint. Only a later
                # change to this graph's cutoff can commit the current summary.
                namespace = tuple(event["namespace"])
                cutoff = projection.compaction_cutoff(event)
                fresh_commit = (
                    namespace in previous_cutoffs and previous_cutoffs[namespace] != cutoff
                )
                previous_cutoffs[namespace] = cutoff
                if (
                    cutoff is not None
                    and fresh_commit
                    and namespace == compaction_namespace
                    and not compaction_done_emitted
                ):
                    compaction_done_emitted = True
                    side_effect_seq += 1
                    yield await emit(
                        projection.compaction_event(
                            run_id=msg_id,
                            thread_id=thread_id,
                            seq=side_effect_seq,
                            state="done",
                            history_id=_compaction_history_id(event),
                            cutoff_index=cutoff[0],
                        )
                    )

            collect_root_ai_message_id(event, msg_id_sink)
            if not repeated_values:
                yield await emit(event)
            for chunk in await delivery.emit_canonical_interrupts(event):
                yield chunk
            if first_token_at is None and event_has_model_content(event):
                first_token_at = time.monotonic()

            usage_event, side_effect_seq = collect_protocol_usage_event(
                event,
                next_seq=side_effect_seq,
                seen_keys=seen_usage_keys,
                usage_sink=usage_sink,
                cost_per_input_token=cost_per_input_token,
                cost_per_output_token=cost_per_output_token,
                started_at=stream_started_at,
                first_token_at=first_token_at,
            )
            if usage_event is not None and not repeated_values:
                yield await emit(usage_event)
            side_effect_events, side_effect_seq = await collect_protocol_side_effect_events(
                event,
                artifact_recorder=artifact_recorder,
                next_seq=side_effect_seq,
            )
            for side_effect_event in side_effect_events:
                yield await emit(side_effect_event)

            if ingested.source == "v3":
                tool_source: StoredProtocolEvent = event
                if continuation and event["method"] == "values" and isinstance(event["data"], dict):
                    messages = event["data"].get("messages")
                    if isinstance(messages, list):
                        # The restart checkpoint baseline already includes tools
                        # completed just before the crash. Rebuild the current
                        # turn's missing phases; older turns stay out of scope.
                        tool_source = {
                            **event,
                            "data": {"messages": current_turn_messages(messages)},
                        }
                for tool_event in synthesize_tool_events_from_values(
                    tool_source,
                    seen_tool_call_ids=seen_synthesized_tool_call_ids,
                    baseline_completed_tool_call_source_identities=(
                        run_metrics.baseline_completed_tool_call_source_identities
                        if run_metrics is not None and continuation is None
                        else ()
                    ),
                    first_seq=delivery.max_emitted_seq + 1,
                ):
                    if tool_phase_already_sent(tool_event):
                        continue
                    yield await emit(tool_event)
                    tool_side_effects, side_effect_seq = await collect_protocol_side_effect_events(
                        tool_event,
                        artifact_recorder=artifact_recorder,
                        next_seq=side_effect_seq,
                    )
                    for side_effect_event in tool_side_effects:
                        yield await emit(side_effect_event)

        if steer_receipts is not None:
            for receipt in await steer_receipts.changed_events(
                first_seq=delivery.max_emitted_seq + 1, force=True
            ):
                if continuation is None or receipt["id"] not in continuation.event_ids:
                    yield await emit(receipt)
        pending_events = await pending_input_requested_events(
            agent,
            config,
            run_id=msg_id,
            thread_id=thread_id,
            emitted=delivery.emitted,
        )
        for pending_event in pending_events:
            yield await emit(pending_event)
        if not pending_events and deferred_empty_input_requested is not None:
            yield await emit(deferred_empty_input_requested)
        yield await emit(
            lifecycle_protocol_event(
                run_id=msg_id,
                thread_id=thread_id,
                seq=delivery.max_emitted_seq + 1,
                event=terminal_lifecycle_event(
                    has_pending_input=delivery.input_requested_emitted or bool(pending_events)
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001 - stream boundary converts failures to wire errors.
        record = StreamErrorRecord(error=exc, message=public_stream_error_message(exc))
        if error_sink is not None:
            error_sink.append(record)
        yield await emit(
            lifecycle_protocol_event(
                run_id=msg_id,
                thread_id=thread_id,
                seq=delivery.max_emitted_seq + 1,
                event="failed",
                error_message=record.message,
            )
        )
        yield await emit(
            error_protocol_event(
                run_id=msg_id,
                thread_id=thread_id,
                seq=delivery.max_emitted_seq + 1,
                exc=exc,
            )
        )
    finally:
        try:
            await events.aclose()
        finally:
            await delivery.close()
