from __future__ import annotations

import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from langgraph.types import Command

from app.agent_runtime import event_names, langgraph_stream_ingestion
from app.agent_runtime import langgraph_event_projection as projection
from app.agent_runtime.event_broker import EventBroker
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
from app.agent_runtime.run_metrics import RunMetricsAccumulator, event_has_model_content
from app.agent_runtime.stream_error_messages import public_stream_error_message
from app.agent_runtime.streaming import (
    ArtifactEventRecorder,
    PersistCallback,
    StreamErrorRecord,
)
from app.config import settings

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
    session_consent_tools: list[str] | None = None,
    run_metrics: RunMetricsAccumulator | None = None,
) -> AsyncGenerator[str, None]:
    msg_id = run_id or str(uuid.uuid4())
    thread_id = langgraph_stream_ingestion.thread_id_from_config(config, msg_id)
    delivery = ProtocolEventDelivery(
        run_id=msg_id,
        trace_sink=trace_sink,
        broker=broker,
        persist_callback=persist_callback,
        session_consent_tools=session_consent_tools,
    )
    side_effect_seq = 0
    deferred_empty_input_requested: StoredProtocolEvent | None = None
    seen_usage_keys: set[tuple[str | None, int, int, int, int, float | None]] = set()
    seen_synthesized_tool_call_ids: set[str] = set()
    compaction_running_emitted = False
    compaction_done_emitted = False
    stream_started_at = time.monotonic()
    first_token_at: float | None = None
    artifact_recorder = await prepare_artifact_recorder(artifact_recorder, run_id=msg_id)

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

    try:
        yield await emit(
            lifecycle_protocol_event(
                run_id=msg_id,
                thread_id=thread_id,
                seq=0,
                event="running",
            )
        )
        if subagent_display_names:
            yield await emit_head_event(
                event_names.SUBAGENT_NAMES,
                {"names": dict(subagent_display_names)},
                "subagent_names",
            )
        if recalled_memories:
            yield await emit_head_event(
                event_names.MEMORY_RECALLED,
                {"memories": recalled_memories},
                "memory_recalled",
            )
        if skill_draft_brief:
            yield await emit_head_event(
                event_names.SKILL_DRAFT,
                skill_draft_brief,
                "skill_draft",
            )

        async for ingested in langgraph_stream_ingestion.iter_ingested_protocol_events(
            agent,
            langgraph_stream_ingestion.actual_input(input_),
            config,
            run_id=msg_id,
            thread_id=thread_id,
        ):
            event = ingested.event
            is_empty_v3_interrupt = (
                ingested.source == "v3" and projection.is_empty_input_requested_event(event)
            )
            if is_empty_v3_interrupt:
                deferred_empty_input_requested = event
                continue

            if settings.compaction_marker_enabled:
                signal = projection.compaction_signal(event)
                if signal == "summary_token":
                    if not compaction_running_emitted:
                        compaction_running_emitted = True
                        side_effect_seq += 1
                        yield await emit(
                            projection.compaction_event(
                                run_id=msg_id,
                                thread_id=thread_id,
                                seq=side_effect_seq,
                                state="running",
                            )
                        )
                    continue
                if signal == "committed" and not compaction_done_emitted:
                    compaction_done_emitted = True
                    side_effect_seq += 1
                    yield await emit(
                        projection.compaction_event(
                            run_id=msg_id,
                            thread_id=thread_id,
                            seq=side_effect_seq,
                            state="done",
                            history_id=_compaction_history_id(event),
                            cutoff_index=projection.compaction_cutoff_index(event),
                        )
                    )

            collect_root_ai_message_id(event, msg_id_sink)
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
            if usage_event is not None:
                yield await emit(usage_event)
            side_effect_events, side_effect_seq = await collect_protocol_side_effect_events(
                event,
                artifact_recorder=artifact_recorder,
                next_seq=side_effect_seq,
            )
            for side_effect_event in side_effect_events:
                yield await emit(side_effect_event)

            if ingested.source == "v3":
                for tool_event in synthesize_tool_events_from_values(
                    event,
                    seen_tool_call_ids=seen_synthesized_tool_call_ids,
                    first_seq=delivery.max_emitted_seq + 1,
                ):
                    yield await emit(tool_event)
                    tool_side_effects, side_effect_seq = await collect_protocol_side_effect_events(
                        tool_event,
                        artifact_recorder=artifact_recorder,
                        next_seq=side_effect_seq,
                    )
                    for side_effect_event in tool_side_effects:
                        yield await emit(side_effect_event)

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
        await delivery.close()
