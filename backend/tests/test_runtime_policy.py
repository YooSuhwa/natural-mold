"""Strict RuntimePolicyV1 parsing, resolution, and canonicalization contracts."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.agent_runtime.runtime_policy import (
    JsonObject,
    JsonValue,
    RuntimePolicyV1,
    canonical_runtime_policy_json,
    resolve_runtime_policy,
)


def test_explicit_v1_defaults_are_behavior_equivalent_to_legacy_null() -> None:
    # Given a legacy NULL row and an explicit v1-default row.
    explicit = {"version": 1}

    # When both cross the strict resolver boundary.
    legacy = resolve_runtime_policy(None)
    stored = resolve_runtime_policy(explicit)

    # Then only provenance differs; the effective policy and digest are identical.
    assert legacy.source == "legacy_compat"
    assert stored.source == "stored"
    assert legacy.effective == stored.effective == RuntimePolicyV1(version=1)
    assert legacy.canonical_json == stored.canonical_json
    assert legacy.policy_hash == stored.policy_hash


def test_explicit_false_survives_strict_parse_and_canonical_serialization() -> None:
    # Given an explicit Todo opt-out alongside defaulted policy sections.
    raw = {"version": 1, "todo": {"enabled": False}}

    # When it is resolved and serialized for JSON persistence.
    resolved = resolve_runtime_policy(raw)

    # Then false remains false and is never collapsed by truthiness.
    assert resolved.effective.todo.enabled is False
    assert resolved.effective.model_dump(mode="json")["todo"] == {"enabled": False}
    assert '"enabled":false' in resolved.canonical_json


@pytest.mark.parametrize("version", [None, True, 1.0, "1", 2])
def test_runtime_policy_rejects_missing_or_non_exact_version(version: JsonValue) -> None:
    # Given a missing or non-exact version discriminator.
    raw: JsonObject = {} if version is None else {"version": version}

    # When the untrusted value crosses the policy boundary.
    with pytest.raises(ValidationError):
        resolve_runtime_policy(raw)

    # Then invalid non-null policy fails closed.


@pytest.mark.parametrize(
    "raw",
    [
        {"version": 1, "unexpected": True},
        {"version": 1, "filesystem": {"mode": "legacy_compat"}},
        {"version": 1, "filesystem": {"mode": "artifact_write", "extra": True}},
        {"version": 1, "filesystem": None},
        {"version": 1, "todo": {"enabled": 0}},
        {"version": 1, "todo": None},
        {"version": 1, "summarization": {"mode": "off"}},
        {"version": 1, "summarization": None},
        {"version": 1, "summarization": {"mode": "preset"}},
        {
            "version": 1,
            "summarization": {"mode": "preset", "preset": "unknown"},
        },
    ],
)
def test_runtime_policy_rejects_unknown_or_malformed_nested_values(
    raw: JsonObject,
) -> None:
    # Given an unknown field, legacy-only mode, coercible bool, or invalid summary choice.

    # When the strict resolver parses it.
    with pytest.raises(ValidationError):
        resolve_runtime_policy(raw)

    # Then the policy cannot enter trusted runtime state.


def test_canonical_json_and_hash_are_deterministic_for_equivalent_input_order() -> None:
    # Given equivalent policies whose untrusted key order differs.
    left = {
        "version": 1,
        "filesystem": {"mode": "inspect"},
        "todo": {"enabled": False},
        "summarization": {"mode": "preset", "preset": "balanced_context_v1"},
    }
    right = {
        "summarization": {"preset": "balanced_context_v1", "mode": "preset"},
        "todo": {"enabled": False},
        "filesystem": {"mode": "inspect"},
        "version": 1,
    }

    # When both are resolved to their canonical UTF-8 JSON representation.
    left_resolved = resolve_runtime_policy(left)
    right_resolved = resolve_runtime_policy(right)
    encoded = left_resolved.canonical_json.encode("utf-8")

    # Then bytes, compact sorted JSON, and SHA-256 are identical.
    assert left_resolved.canonical_json == right_resolved.canonical_json
    assert left_resolved.policy_hash == right_resolved.policy_hash
    assert left_resolved.canonical_json == (
        '{"filesystem":{"mode":"inspect"},'
        '"summarization":{"mode":"preset","preset":"balanced_context_v1"},'
        '"todo":{"enabled":false},"version":1}'
    )
    assert left_resolved.policy_hash == hashlib.sha256(encoded).hexdigest()
    assert canonical_runtime_policy_json(left_resolved.effective) == left_resolved.canonical_json
