from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NotRequired, TypedDict

from app.agent_runtime.protocol_events import StoredProtocolEvent, stored_protocol_event


class UsagePayload(TypedDict):
    run_id: str
    prompt_tokens: int
    completion_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    assistant_msg_id: NotRequired[str]
    estimated_cost: NotRequired[float]


class _UsageCandidate(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    assistant_msg_id: str | None
    estimated_cost: float | None


def collect_protocol_usage_event(
    event: StoredProtocolEvent,
    *,
    next_seq: int,
    seen_keys: set[tuple[str | None, int, int, int, int, float | None]],
    usage_sink: dict[str, Any] | None,
    cost_per_input_token: float | None,
    cost_per_output_token: float | None,
) -> tuple[StoredProtocolEvent | None, int]:
    candidate = _usage_candidate_from_event(
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
    if usage_sink is not None:
        usage_sink.update(sink_payload)

    seq = max(next_seq, event["seq"]) + 1
    payload: UsagePayload = {"run_id": event["run_id"], **sink_payload}
    if candidate["assistant_msg_id"] is not None:
        payload["assistant_msg_id"] = candidate["assistant_msg_id"]

    event_id = f"{event['id']}:usage"
    return (
        stored_protocol_event(
            run_id=event["run_id"],
            thread_id=event["thread_id"],
            seq=seq,
            method="custom:usage",
            data=payload,
            namespace=event["namespace"],
            event_id=event_id,
            id=event_id,
            timestamp=event["timestamp"],
        ),
        seq,
    )


def _usage_candidate_from_event(
    event: StoredProtocolEvent,
    *,
    cost_per_input_token: float | None,
    cost_per_output_token: float | None,
) -> _UsageCandidate | None:
    if event["method"] not in {"messages", "values"}:
        return None
    return _usage_candidate_from_value(
        event["data"],
        cost_per_input_token=cost_per_input_token,
        cost_per_output_token=cost_per_output_token,
    )


def _usage_candidate_from_value(
    value: Any,
    *,
    cost_per_input_token: float | None,
    cost_per_output_token: float | None,
) -> _UsageCandidate | None:
    if isinstance(value, Mapping):
        direct = _usage_candidate_from_mapping(
            value,
            cost_per_input_token=cost_per_input_token,
            cost_per_output_token=cost_per_output_token,
        )
        if direct is not None:
            return direct
        for child in _message_like_children(value):
            found = _usage_candidate_from_value(
                child,
                cost_per_input_token=cost_per_input_token,
                cost_per_output_token=cost_per_output_token,
            )
            if found is not None:
                return found
        return None

    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for child in reversed(value):
            found = _usage_candidate_from_value(
                child,
                cost_per_input_token=cost_per_input_token,
                cost_per_output_token=cost_per_output_token,
            )
            if found is not None:
                return found
    return None


def _usage_candidate_from_mapping(
    value: Mapping[str, Any],
    *,
    cost_per_input_token: float | None,
    cost_per_output_token: float | None,
) -> _UsageCandidate | None:
    metadata = value.get("usage_metadata")
    if isinstance(metadata, Mapping):
        return _candidate_from_usage_mapping(
            metadata,
            assistant_msg_id=_text_value(value.get("id")),
            cost_per_input_token=cost_per_input_token,
            cost_per_output_token=cost_per_output_token,
        )

    usage = value.get("usage")
    if isinstance(usage, Mapping):
        return _candidate_from_usage_mapping(
            usage,
            assistant_msg_id=_text_value(value.get("id") or value.get("assistant_msg_id")),
            cost_per_input_token=cost_per_input_token,
            cost_per_output_token=cost_per_output_token,
        )

    response_metadata = value.get("response_metadata")
    if isinstance(response_metadata, Mapping):
        token_usage = response_metadata.get("token_usage")
        if isinstance(token_usage, Mapping):
            return _candidate_from_usage_mapping(
                token_usage,
                assistant_msg_id=_text_value(value.get("id")),
                cost_per_input_token=cost_per_input_token,
                cost_per_output_token=cost_per_output_token,
            )
    return None


def _candidate_from_usage_mapping(
    usage: Mapping[str, Any],
    *,
    assistant_msg_id: str | None,
    cost_per_input_token: float | None,
    cost_per_output_token: float | None,
) -> _UsageCandidate | None:
    input_details = _mapping_value(usage.get("input_token_details"))
    prompt_details = _mapping_value(usage.get("prompt_tokens_details"))
    prompt = _int_value(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("tokens_in")
        or usage.get("total_input_tokens")
    )
    completion = _int_value(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("tokens_out")
        or usage.get("total_output_tokens")
    )
    cache_creation = _int_value(
        input_details.get("cache_creation")
        or input_details.get("cache_creation_tokens")
        or usage.get("cache_creation_tokens")
    )
    cache_read = _int_value(
        input_details.get("cache_read")
        or input_details.get("cache_read_tokens")
        or prompt_details.get("cached_tokens")
        or usage.get("cache_read_tokens")
    )
    if prompt == 0 and completion == 0 and cache_creation == 0 and cache_read == 0:
        return None

    estimated_cost = _estimated_cost(
        usage,
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_per_input_token=cost_per_input_token,
        cost_per_output_token=cost_per_output_token,
    )
    return {
        "assistant_msg_id": assistant_msg_id,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cache_creation_tokens": cache_creation,
        "cache_read_tokens": cache_read,
        "estimated_cost": estimated_cost,
    }


def _message_like_children(value: Mapping[str, Any]) -> list[Any]:
    children: list[Any] = []
    for key in ("messages", "message", "chunk", "payload"):
        child = value.get(key)
        if child is not None:
            children.append(child)
    return list(reversed(children))


def _sink_payload(candidate: _UsageCandidate) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prompt_tokens": candidate["prompt_tokens"],
        "completion_tokens": candidate["completion_tokens"],
        "cache_creation_tokens": candidate["cache_creation_tokens"],
        "cache_read_tokens": candidate["cache_read_tokens"],
    }
    if candidate["estimated_cost"] is not None:
        payload["estimated_cost"] = candidate["estimated_cost"]
    return payload


def _estimated_cost(
    usage: Mapping[str, Any],
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cost_per_input_token: float | None,
    cost_per_output_token: float | None,
) -> float | None:
    if cost_per_input_token is not None or cost_per_output_token is not None:
        cost = (prompt_tokens * (cost_per_input_token or 0)) + (
            completion_tokens * (cost_per_output_token or 0)
        )
        return round(cost, 8) if cost > 0 else 0.0
    return _float_value(usage.get("estimated_cost") or usage.get("cost_usd"))


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
