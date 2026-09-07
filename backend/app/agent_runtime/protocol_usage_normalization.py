"""Single protocol-message and provider-usage normalization contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from app.agent_runtime.protocol_events import StoredProtocolEvent


class UsageCandidate(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    assistant_msg_id: str | None
    estimated_cost: float | None


def merge_usage_candidate(
    prior: UsageCandidate | None,
    current: UsageCandidate,
) -> UsageCandidate:
    """Merge cumulative provider snapshots without double-counting replayed values."""
    if prior is None:
        return current
    prior_cost = prior["estimated_cost"]
    current_cost = current["estimated_cost"]
    if prior_cost is None:
        estimated_cost = current_cost
    elif current_cost is None:
        estimated_cost = prior_cost
    else:
        estimated_cost = max(prior_cost, current_cost)
    return {
        "assistant_msg_id": current["assistant_msg_id"],
        "prompt_tokens": max(prior["prompt_tokens"], current["prompt_tokens"]),
        "completion_tokens": max(prior["completion_tokens"], current["completion_tokens"]),
        "cache_creation_tokens": max(
            prior["cache_creation_tokens"], current["cache_creation_tokens"]
        ),
        "cache_read_tokens": max(prior["cache_read_tokens"], current["cache_read_tokens"]),
        "estimated_cost": estimated_cost,
    }


def protocol_message_mappings(event: StoredProtocolEvent) -> list[Mapping[str, Any]]:
    """Return normalized message-like mappings from stored protocol shapes."""
    if event["method"] == "custom" and isinstance(event["data"], Mapping):
        if event["data"].get("name") != "usage":
            return []
        payload = event["data"].get("payload")
        return [payload] if isinstance(payload, Mapping) else []
    if event["method"] not in {"messages", "values"}:
        return []
    return _nested_message_mappings(event["data"])


def usage_candidate_from_event(
    event: StoredProtocolEvent,
    *,
    cost_per_input_token: float | None,
    cost_per_output_token: float | None,
) -> UsageCandidate | None:
    """Return the latest usable provider snapshot from a messages/values event."""
    if event["method"] not in {"messages", "values"}:
        return None
    for message in reversed(protocol_message_mappings(event)):
        candidate = usage_candidate_from_mapping(
            message,
            cost_per_input_token=cost_per_input_token,
            cost_per_output_token=cost_per_output_token,
        )
        if candidate is not None:
            return candidate
    return None


def usage_candidate_from_mapping(
    message: Mapping[str, Any],
    *,
    cost_per_input_token: float | None = None,
    cost_per_output_token: float | None = None,
) -> UsageCandidate | None:
    usage = usage_mapping(message)
    if usage is None:
        return None
    prompt_tokens = _nonnegative_int(
        usage,
        "input_tokens",
        "prompt_tokens",
        "tokens_in",
        "total_input_tokens",
    )
    completion_tokens = _nonnegative_int(
        usage,
        "output_tokens",
        "completion_tokens",
        "tokens_out",
        "total_output_tokens",
    )
    if prompt_tokens is None or completion_tokens is None:
        return None
    input_details = _mapping_value(usage.get("input_token_details"))
    prompt_details = _mapping_value(usage.get("prompt_tokens_details"))
    cache_creation_tokens = _nonnegative_int_from_mappings(
        (input_details, usage),
        "cache_creation",
        "cache_creation_tokens",
    )
    cache_read_tokens = _nonnegative_int_from_mappings(
        (input_details, prompt_details, usage),
        "cache_read",
        "cache_read_tokens",
        "cached_tokens",
    )
    if (
        prompt_tokens == 0
        and completion_tokens == 0
        and cache_creation_tokens == 0
        and cache_read_tokens == 0
    ):
        return None
    return {
        "assistant_msg_id": text_value(message.get("id") or message.get("assistant_msg_id")),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_read_tokens": cache_read_tokens,
        "estimated_cost": _estimated_cost(
            usage,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_per_input_token=cost_per_input_token,
            cost_per_output_token=cost_per_output_token,
        ),
    }


def usage_mapping(message: Mapping[str, Any]) -> Mapping[str, Any] | None:
    metadata = message.get("usage_metadata")
    if isinstance(metadata, Mapping):
        return metadata
    usage = message.get("usage")
    if isinstance(usage, Mapping):
        return usage
    response_metadata = message.get("response_metadata")
    if isinstance(response_metadata, Mapping):
        token_usage = response_metadata.get("token_usage")
        if isinstance(token_usage, Mapping):
            return token_usage
    if all(key in message for key in ("prompt_tokens", "completion_tokens")):
        return message
    return None


def is_model_message(message: Mapping[str, Any]) -> bool:
    message_type = text_value(message.get("type"))
    return message_type is not None and message_type.lower().startswith("ai")


def has_content(message: Mapping[str, Any]) -> bool:
    content = message.get("content")
    if isinstance(content, str):
        return bool(content)
    if isinstance(content, Sequence) and not isinstance(content, bytes | bytearray):
        return bool(content)
    return False


def text_value(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _nested_message_mappings(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if usage_candidate_from_mapping(value) is not None:
            return [value]
        metadata = value.get("metadata")
        if isinstance(metadata, Mapping) and usage_candidate_from_mapping(metadata) is not None:
            nested_usage = dict(metadata)
            for key in ("id", "assistant_msg_id", "content", "type"):
                if key not in nested_usage and key in value:
                    nested_usage[key] = value[key]
            return [nested_usage]
        if is_model_message(value):
            return [value]
        matches: list[Mapping[str, Any]] = []
        for key in ("messages", "message", "chunk", "payload", "metadata"):
            child = value.get(key)
            if child is not None:
                matches.extend(_nested_message_mappings(child))
        return matches
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        matches = []
        for child in value:
            matches.extend(_nested_message_mappings(child))
        return matches
    return []


def _nonnegative_int(mapping: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None
    return None


def _nonnegative_int_from_mappings(mappings: Sequence[Any], *keys: str) -> int:
    for mapping in mappings:
        if not isinstance(mapping, Mapping):
            continue
        parsed = _nonnegative_int(mapping, *keys)
        if parsed is not None:
            return parsed
    return 0


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
    return _nonnegative_float(usage, "estimated_cost", "cost_usd")


def _nonnegative_float(mapping: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None
    return None


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
