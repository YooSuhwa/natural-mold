"""Portable Agent Blueprint runtime-policy validation and canonicalization."""

from __future__ import annotations

from typing import Any

from app.agent_runtime.runtime_policy import RuntimePolicyV1, runtime_policy_to_json
from app.error_codes import marketplace_invalid_package

_RESERVED_RUNTIME_METADATA_KEYS = frozenset(
    {
        "effective",
        "hash",
        "runtime_policy_source",
        "runtime_policy_effective",
        "runtime_policy_snapshot",
        "runtime_policy_hash",
        "runtime_policy_version",
        "server_owned",
        "snapshot",
        "source",
        "version",
    }
)


def parse_portable_runtime_policy(value: Any) -> RuntimePolicyV1:
    """Parse one concrete marketplace policy without accepting opaque JSON."""
    try:
        return RuntimePolicyV1.model_validate(value)
    except (TypeError, ValueError):
        raise marketplace_invalid_package("runtime policy is malformed") from None


def canonicalize_portable_runtime_policy(value: Any) -> dict[str, Any]:
    """Return the stable JSON form of a concrete portable policy."""
    return runtime_policy_to_json(parse_portable_runtime_policy(value))


def canonicalize_agent_spec_runtime_policy(agent_spec: dict[str, Any]) -> dict[str, Any]:
    """Preserve absent/null policy fields while canonicalizing concrete v1 data."""
    if _RESERVED_RUNTIME_METADATA_KEYS.intersection(agent_spec):
        raise marketplace_invalid_package("runtime metadata is not portable")
    normalized = dict(agent_spec)
    if "runtime_policy" not in normalized or normalized["runtime_policy"] is None:
        return normalized
    normalized["runtime_policy"] = canonicalize_portable_runtime_policy(
        normalized["runtime_policy"]
    )
    return normalized


def canonicalize_agent_blueprint_runtime_policy(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize only the portable policy field before a blueprint is persisted."""
    if _RESERVED_RUNTIME_METADATA_KEYS.intersection(payload):
        raise marketplace_invalid_package("runtime metadata is not portable")
    agent_spec = payload.get("agent")
    if not isinstance(agent_spec, dict):
        return dict(payload)
    normalized = dict(payload)
    normalized["agent"] = canonicalize_agent_spec_runtime_policy(agent_spec)
    return normalized
