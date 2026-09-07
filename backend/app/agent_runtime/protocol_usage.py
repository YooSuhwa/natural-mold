from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_custom_protocol_event
from app.agent_runtime.protocol_usage_normalization import (
    UsageCandidate,
    usage_candidate_from_event,
)
from app.agent_runtime.usage_timing import compute_usage_timing


class UsageMetricsPayload(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    estimated_cost: NotRequired[float]
    # 스트리밍 timing (live-only) — usage 옆에 실어 같은 경로로 흐른다.
    ttft_ms: NotRequired[float]
    generation_ms: NotRequired[float]
    tokens_per_second: NotRequired[float]


class UsagePayload(UsageMetricsPayload):
    run_id: str
    assistant_msg_id: NotRequired[str]


def collect_protocol_usage_event(
    event: StoredProtocolEvent,
    *,
    next_seq: int,
    seen_keys: set[tuple[str | None, int, int, int, int, float | None]],
    usage_sink: dict[str, Any] | None,
    cost_per_input_token: float | None,
    cost_per_output_token: float | None,
    started_at: float | None = None,
    first_token_at: float | None = None,
) -> tuple[StoredProtocolEvent | None, int]:
    candidate = usage_candidate_from_event(
        event,
        cost_per_input_token=cost_per_input_token,
        cost_per_output_token=cost_per_output_token,
    )
    if candidate is None:
        return None, next_seq

    key = (
        candidate["assistant_msg_id"],
        candidate["prompt_tokens"],
        candidate["completion_tokens"],
        candidate["cache_creation_tokens"],
        candidate["cache_read_tokens"],
        candidate["estimated_cost"],
    )
    if key in seen_keys:
        return None, next_seq
    seen_keys.add(key)

    sink_payload = _sink_payload(candidate)
    if started_at is not None:
        timing = compute_usage_timing(
            started_at=started_at,
            first_token_at=first_token_at,
            completion_tokens=candidate["completion_tokens"],
        )
        if "ttft_ms" in timing:
            sink_payload["ttft_ms"] = timing["ttft_ms"]
        sink_payload["generation_ms"] = timing["generation_ms"]
        if first_token_at is not None and "tokens_per_second" in timing:
            sink_payload["tokens_per_second"] = timing["tokens_per_second"]
    if usage_sink is not None:
        usage_sink.update(sink_payload)

    seq = max(next_seq, event["seq"]) + 1
    payload: UsagePayload = {
        "run_id": event["run_id"],
        "prompt_tokens": sink_payload["prompt_tokens"],
        "completion_tokens": sink_payload["completion_tokens"],
        "cache_creation_tokens": sink_payload["cache_creation_tokens"],
        "cache_read_tokens": sink_payload["cache_read_tokens"],
    }
    if "estimated_cost" in sink_payload:
        payload["estimated_cost"] = sink_payload["estimated_cost"]
    if candidate["assistant_msg_id"] is not None:
        payload["assistant_msg_id"] = candidate["assistant_msg_id"]
    if "ttft_ms" in sink_payload:
        payload["ttft_ms"] = sink_payload["ttft_ms"]
    if "generation_ms" in sink_payload:
        payload["generation_ms"] = sink_payload["generation_ms"]
    if "tokens_per_second" in sink_payload:
        payload["tokens_per_second"] = sink_payload["tokens_per_second"]

    event_id = f"{event['id']}:usage"
    return (
        stored_custom_protocol_event(
            run_id=event["run_id"],
            thread_id=event["thread_id"],
            seq=seq,
            name="usage",
            payload=payload,
            namespace=event["namespace"],
            event_id=event_id,
            id=event_id,
            timestamp=event["timestamp"],
        ),
        seq,
    )


def _sink_payload(candidate: UsageCandidate) -> UsageMetricsPayload:
    payload: UsageMetricsPayload = {
        "prompt_tokens": candidate["prompt_tokens"],
        "completion_tokens": candidate["completion_tokens"],
        "cache_creation_tokens": candidate["cache_creation_tokens"],
        "cache_read_tokens": candidate["cache_read_tokens"],
    }
    if candidate["estimated_cost"] is not None:
        payload["estimated_cost"] = candidate["estimated_cost"]
    return payload
