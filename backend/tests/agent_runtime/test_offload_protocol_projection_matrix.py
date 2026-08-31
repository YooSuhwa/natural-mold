"""Trusted-root string projection contracts."""

from __future__ import annotations

from pathlib import Path

from app.agent_runtime.offload_protocol_projection import project_offload_egress_data
from app.agent_runtime.offload_storage_types import OffloadKind, logical_offload_id

_OWNER = "a" * 32
_CONVERSATION = "b" * 32
_RUN = "c" * 32
_ACTOR = "d" * 32
_LEAF = "e" * 32


def test_known_root_with_spaces_and_parentheses_preserves_surrounding_context(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Moldy Data (private)"
    history = root / f".moldy-internal/offload/history/{_OWNER}/{_CONVERSATION}/{_LEAF}"
    spill = root / (
        f".moldy-internal/offload/spill/{_OWNER}/{_CONVERSATION}/{_RUN}/{_ACTOR}/{_LEAF}"
    )
    raw = f"before ({history}), then [{spill}]; artifact=/conversations/report.md"

    projected = project_offload_egress_data(raw, roots=(root,))

    assert projected == (
        f"before ({logical_offload_id(OffloadKind.HISTORY, str(history))}), then "
        f"[{logical_offload_id(OffloadKind.SPILL, str(spill))}]; "
        "artifact=/conversations/report.md"
    )


def test_multiple_known_root_references_are_projected_independently(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    first = root / f".moldy-internal/offload/history/{_OWNER}/{_CONVERSATION}/{_RUN}"
    second = root / f".moldy-internal/offload/history/{_OWNER}/{_CONVERSATION}/{_ACTOR}"
    raw = f"first={first}\nsecond={second}"

    projected = project_offload_egress_data(raw, roots=(root,))

    assert projected == (
        f"first={logical_offload_id(OffloadKind.HISTORY, str(first))}\n"
        f"second={logical_offload_id(OffloadKind.HISTORY, str(second))}"
    )


def test_injected_roots_are_frozen_before_recursive_projection(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    path = root / (
        f".moldy-internal/offload/spill/{_OWNER}/{_CONVERSATION}/{_RUN}/{_ACTOR}/{_LEAF}"
    )
    roots = [root]
    raw = {"first": str(path), "nested": [str(path)]}

    projected = project_offload_egress_data(raw, roots=roots)
    roots.clear()

    logical_id = logical_offload_id(OffloadKind.SPILL, str(path))
    assert projected == {"first": logical_id, "nested": [logical_id]}


def test_configured_root_invalid_topology_fails_closed_without_hashing(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    path = root / f".moldy-internal/offload/spill/{_OWNER}/{_CONVERSATION}/{_LEAF}"

    projected = project_offload_egress_data(f"path={path}; secret=OPAQUE", roots=(root,))

    assert projected == "internal_reference_redacted"
    assert "spill_" not in projected


def test_direct_leak_reproduction_fails_closed_for_unknown_prefix() -> None:
    raw = "prefix /srv/a (b)/runtime/.moldy-internal/offload/history/o/c/a/s.md (tail)"

    projected = project_offload_egress_data(raw, roots=())

    assert projected == "internal_reference_redacted"
    assert "/srv/a (" not in projected


def test_unknown_marker_with_secret_redacts_entire_scalar_without_hash_oracle() -> None:
    first = (
        "prefix=alpha; path=/unknown/.moldy-internal/offload/spill/a/b/c/d/result; "
        "secret=FIRST_SECRET"
    )
    second = first.replace("FIRST_SECRET", "SECOND_SECRET")

    first_projected = project_offload_egress_data(first, roots=())
    second_projected = project_offload_egress_data(second, roots=())

    assert first_projected == second_projected == "internal_reference_redacted"
    assert "prefix" not in first_projected
    assert "SECRET" not in first_projected
    assert "history_" not in first_projected
    assert "spill_" not in first_projected
