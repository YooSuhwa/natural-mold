"""PreparedRunLease is process-local and its digest is deterministic and non-secret."""

from __future__ import annotations

import copy
import dataclasses
import pickle
import uuid

import pytest

from app.agent_runtime.prepared_run_digest import (
    canonical_digest_bytes,
    compute_prepared_run_digest,
)
from tests.authz import prepared_run_testkit as kit


def test_lease_refuses_serialization_and_copy() -> None:
    lease = kit.lease(kit.binding(), "token")

    with pytest.raises(TypeError, match="process-local"):
        pickle.dumps(lease)
    with pytest.raises(TypeError, match="process-local"):
        copy.copy(lease)
    assert kit.SECRET not in repr(lease)


def test_digest_is_deterministic_across_mapping_order() -> None:
    run_binding = kit.binding()
    first = kit.material(run_binding)
    reordered = kit.material(
        run_binding,
        versions={"runtime_policy": "sha-policy", "agent_lifecycle": "3"},
        tool_access={"write_file": "run_output", "search": "resolved_external"},
        secret_reference_ids=("credential:search", "credential:llm"),
    )

    assert compute_prepared_run_digest(first) == compute_prepared_run_digest(reordered)
    assert len(compute_prepared_run_digest(first)) == 64


@pytest.mark.parametrize(
    "override",
    [
        {"versions": {"agent_lifecycle": "4", "runtime_policy": "sha-policy"}},
        {"snapshot_digests": {"skill:writer": "sha-other", "memory": "sha-memory"}},
        {"tool_access": {"search": "live_application_resource", "write_file": "run_output"}},
        {"checkpoint_lineage": ("thread", "", "checkpoint-2")},
        {"secret_reference_ids": ("credential:llm",)},
    ],
)
def test_digest_changes_with_any_bound_field(override: dict[str, object]) -> None:
    run_binding = kit.binding()

    assert compute_prepared_run_digest(kit.material(run_binding)) != compute_prepared_run_digest(
        kit.material(run_binding, **override)
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"run_id": uuid.UUID("60000000-0000-4000-8000-000000000099")},
        {"thread_id": "other-thread"},
        {"participant_paths": ("root",)},
    ],
)
def test_digest_changes_with_binding_identity(changes: dict[str, object]) -> None:
    run_binding = kit.binding()
    first = kit.material(run_binding)
    other = kit.material(dataclasses.replace(run_binding, **changes))

    assert compute_prepared_run_digest(first) != compute_prepared_run_digest(other)


def test_lease_is_immutable() -> None:
    lease = kit.lease(kit.binding(), "token")

    with pytest.raises(dataclasses.FrozenInstanceError):
        lease.digest = "tampered"  # type: ignore[misc]


@pytest.mark.parametrize(
    "override",
    [
        {"versions": {"agent_lifecycle": 3}},
        {"checkpoint_lineage": ("thread", "")},
        {"checkpoint_lineage": ("thread", "", 7)},
    ],
)
def test_digest_rejects_non_string_material(override: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="digest_field_not_string"):
        compute_prepared_run_digest(kit.material(kit.binding(), **override))


def test_digest_bytes_contain_no_secret_and_reject_unknown_tool_class() -> None:
    run_binding = kit.binding()
    payload = canonical_digest_bytes(kit.material(run_binding))

    assert kit.SECRET.encode() not in payload
    assert b"credential:llm" in payload
    with pytest.raises(ValueError, match="tool_access_class_unknown"):
        compute_prepared_run_digest(kit.material(run_binding, tool_access={"x": "anything"}))
