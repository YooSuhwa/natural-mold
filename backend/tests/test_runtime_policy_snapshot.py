from __future__ import annotations

import pytest

from app.agent_runtime.runtime_policy import (
    ASSISTANT_RUNTIME_POLICY,
    LEGACY_RUNTIME_POLICY,
    SKILL_BUILDER_RUNTIME_POLICY,
    RuntimePolicySnapshotError,
    resolve_runtime_policy,
    runtime_policy_to_json,
    validate_runtime_policy_snapshot,
)
from app.schemas.conversation_refs import JsonValue


def test_snapshot_validator_accepts_complete_stored_tuple_with_explicit_false() -> None:
    # Given a non-default stored policy whose false value must survive persistence.
    resolved = resolve_runtime_policy({"version": 1, "todo": {"enabled": False}})

    # When the four-field snapshot is parsed.
    snapshot = validate_runtime_policy_snapshot(
        runtime_policy_to_json(resolved.effective),
        1,
        resolved.policy_hash,
        resolved.source,
    )

    # Then the trusted immutable value preserves the explicit false.
    assert snapshot is not None
    assert snapshot.effective.todo.enabled is False
    assert snapshot.source == "stored"


@pytest.mark.parametrize(
    "values",
    [
        ({"version": 1}, None, None, None),
        (None, 1, LEGACY_RUNTIME_POLICY.policy_hash, "legacy_compat"),
        ({"version": 1}, 1, LEGACY_RUNTIME_POLICY.policy_hash, "legacy_compat"),
        (
            runtime_policy_to_json(LEGACY_RUNTIME_POLICY.effective),
            1,
            "0" * 64,
            "legacy_compat",
        ),
    ],
)
def test_snapshot_validator_rejects_partial_or_mismatched_tuple(
    values: tuple[dict[str, JsonValue] | None, int | None, str | None, str | None],
) -> None:
    # Given a partial or internally inconsistent persisted tuple.
    # When it crosses the snapshot boundary.
    with pytest.raises(RuntimePolicySnapshotError) as exc:
        validate_runtime_policy_snapshot(*values)

    # Then the bounded error exposes no persisted values.
    assert str(exc.value) == "RUNTIME_POLICY_SNAPSHOT_INVALID"


@pytest.mark.parametrize("source", ["legacy_compat", "server_owned"])
def test_compatibility_sources_reject_non_default_policy(source: str) -> None:
    # Given a custom policy falsely labelled as compatibility-owned.
    custom = resolve_runtime_policy({"version": 1, "todo": {"enabled": False}})

    # When it is parsed as a snapshot.
    with pytest.raises(RuntimePolicySnapshotError):
        validate_runtime_policy_snapshot(
            runtime_policy_to_json(custom.effective),
            1,
            custom.policy_hash,
            source,
        )


def test_server_owned_policies_are_distinct_named_immutable_values() -> None:
    # Given the two server-owned runtime surfaces.
    # When their policy values are inspected.
    # Then both use the canonical compatibility behavior but remain separately named objects.
    assert ASSISTANT_RUNTIME_POLICY is not SKILL_BUILDER_RUNTIME_POLICY
    assert ASSISTANT_RUNTIME_POLICY.source == "server_owned"
    assert SKILL_BUILDER_RUNTIME_POLICY.source == "server_owned"
    assert ASSISTANT_RUNTIME_POLICY.policy_hash == LEGACY_RUNTIME_POLICY.policy_hash
