"""Receipt classification and filesystem tests for cleanup discovery."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, assert_never

import pytest

from tests.cleanup_discovery_support import (
    JSONObject,
    ManifestValidationError,
    claim_parser,
    discovery,
    e2e_payload,
    postgres_payload,
    write_payload,
)
from tests.cleanup_discovery_support import (
    absent_probes_fixture as _absent_probes_fixture,  # noqa: F401
)


def test_discovery_ignores_history_and_collects_only_lifecycle_claims(
    tmp_path: Path, absent_probes: Path
) -> None:
    # Given: mixed historical metadata and two lifecycle receipts.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    write_payload(root, "aggregate.json", {"status": "passed", "children": []})
    write_payload(root, "z-postgres.json", postgres_payload(absent_probes))
    write_payload(root, "a-e2e.json", e2e_payload())

    # When: direct evidence children are discovered and live residue is probed.
    summary = discovery.discover_and_probe(root)

    # Then: only lifecycle claims are reported in deterministic order.
    assert summary.receipts == ("a-e2e.json", "z-postgres.json")


@pytest.mark.parametrize(
    "metadata",
    [
        {"schema_version": 1, "mode": "analysis"},
        {"schema_version": 1, "scenarios": [{"name": "documentation"}]},
        {
            "schema_version": 1,
            "mode": "analysis",
            "scenarios": [{"name": "documentation"}],
        },
        {"schema_version": 1, "mode": "unknown", "scenarios": []},
    ],
)
def test_discovery_ignores_unrecognized_metadata_shapes(
    tmp_path: Path, absent_probes: Path, metadata: JSONObject
) -> None:
    # Given: ordinary evidence metadata reuses one generic lifecycle field name.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    write_payload(root, "metadata.json", metadata)

    # When: residue-only discovery classifies direct JSON children.
    summary = discovery.discover_and_probe(root)

    # Then: a lone generic field does not claim the PostgreSQL lifecycle contract.
    assert summary.receipts == ()


def test_discovery_accepts_legacy_receipt(tmp_path: Path, absent_probes: Path) -> None:
    # Given: a legacy receipt lacks modern observation booleans and a process hash.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    write_payload(
        root,
        "legacy.json",
        {
            "schema_version": 1,
            "mode": "all",
            "scenarios": [
                {
                    "process_id": 12345,
                    "run_root": str(absent_probes / ".moldy-pg-run-legacy"),
                    "port": 49153,
                }
            ],
        },
    )

    # When: residue-only discovery reads the historical schema.
    summary = discovery.discover_and_probe(root)

    # Then: the safe legacy claim participates without retrospective validation.
    assert summary.receipts == ("legacy.json",)


@pytest.mark.parametrize(
    "payload",
    [
        {"runner": "moldy-isolated-e2e", "run_id": "bad", "lane": "scripted"},
        {"mode": "all", "scenarios": "bad"},
        {
            "mode": "all",
            "scenarios": [{"process_id": "bad", "process_identity_sha256": "c" * 64}],
        },
    ],
)
def test_discovery_rejects_malformed_claimed_lifecycle(
    tmp_path: Path, absent_probes: Path, payload: JSONObject
) -> None:
    # Given: a JSON file claims lifecycle ownership with malformed identities.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    write_payload(root, "claim.json", payload)

    # When/Then: discovery fails with a stable, non-reflective reason.
    with pytest.raises(ManifestValidationError, match=r"^discovery_claim$"):
        discovery.discover_and_probe(root)


type UnsafeEntryKind = Literal["symlink", "hardlink", "writable", "fifo"]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "writable", "fifo"])
def test_discovery_rejects_unsafe_json_entries(
    tmp_path: Path, absent_probes: Path, kind: UnsafeEntryKind
) -> None:
    # Given: a direct JSON child has an unsafe filesystem identity.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    target = write_payload(root, "target.txt", {"status": "passed"})
    entry = root / "unsafe.json"
    match kind:
        case "symlink":
            entry.symlink_to(target)
        case "hardlink":
            os.link(target, entry)
        case "writable":
            entry.write_text("{}", encoding="utf-8")
            entry.chmod(0o622)
        case "fifo":
            os.mkfifo(entry)
        case _ as unreachable:
            assert_never(unreachable)

    # When/Then: unsafe evidence is rejected before probing.
    with pytest.raises(ManifestValidationError, match=r"^discovery_file$"):
        discovery.discover_and_probe(root)


def test_discovery_detects_root_mutation_at_race_seam(tmp_path: Path, absent_probes: Path) -> None:
    # Given: a root is mutated after its initial directory snapshot.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    write_payload(root, "history.json", {"status": "passed"})

    # When/Then: identity revalidation fails closed.
    with pytest.raises(ManifestValidationError, match=r"^discovery_root_changed$"):
        discovery.discover_and_probe(
            root,
            after_read=lambda: write_payload(root, "late.json", {"status": "passed"}),
        )


def test_discovery_detects_same_size_file_rewrite_at_race_seam(
    tmp_path: Path, absent_probes: Path
) -> None:
    # Given: a lifecycle receipt can be rewritten in place without changing its size.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    write_payload(root, "e2e.json", e2e_payload("a" * 24))

    # When/Then: post-read metadata revalidation detects the changed file identity.
    with pytest.raises(ManifestValidationError, match=r"^discovery_file_changed$"):
        discovery.discover_and_probe(
            root,
            after_read=lambda: write_payload(root, "e2e.json", e2e_payload("b" * 24)),
        )


def test_discovery_rejects_writable_evidence_root(tmp_path: Path, absent_probes: Path) -> None:
    # Given: a group-writable evidence trust root.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o770)
    root.chmod(0o770)

    # When/Then: discovery rejects the root before reading children.
    with pytest.raises(ManifestValidationError, match=r"^discovery_root$"):
        discovery.discover_and_probe(root)


def test_discovery_rejects_malformed_json(tmp_path: Path, absent_probes: Path) -> None:
    # Given: malformed direct JSON cannot be safely classified.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    entry = root / "broken.json"
    entry.write_text("{", encoding="utf-8")
    entry.chmod(0o600)

    # When/Then: discovery fails closed without reflecting file content.
    with pytest.raises(ManifestValidationError, match=r"^discovery_json$"):
        discovery.discover_and_probe(root)


def test_discovery_enforces_bounded_aggregate_reads(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: valid JSON files exceed a deliberately narrowed aggregate budget.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    write_payload(root, "one.json", {"padding": "a" * 32})
    write_payload(root, "two.json", {"padding": "b" * 32})
    monkeypatch.setattr("cleanup_discovery.MAX_TOTAL_BYTES", 40)

    # When/Then: bounded discovery rejects the aggregate before probes run.
    with pytest.raises(ManifestValidationError, match=r"^discovery_limit$"):
        discovery.discover_and_probe(root)


def test_discovery_bounds_scenarios_per_receipt(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: one lifecycle receipt contains more scenarios than the narrowed claim budget.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    payload = postgres_payload(absent_probes)
    scenarios = payload["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[0]
    assert isinstance(scenario, dict)
    scenarios.append(dict(scenario))
    write_payload(root, "postgres.json", payload)
    monkeypatch.setattr(claim_parser, "MAX_CLAIMS", 1)

    # When/Then: discovery rejects before building an oversized claim set.
    with pytest.raises(ManifestValidationError, match=r"^discovery_limit$"):
        discovery.discover_and_probe(root)


def test_discovery_bounds_merged_claims(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: individually small receipts exceed the narrowed aggregate claim budget.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    one = postgres_payload(absent_probes)
    two = postgres_payload(absent_probes)
    two_scenarios = two["scenarios"]
    assert isinstance(two_scenarios, list)
    second = two_scenarios[0]
    assert isinstance(second, dict)
    second["process_id"] = 12346
    second["run_root"] = str(absent_probes / ".moldy-test-run.second")
    second["port"] = 49153
    write_payload(root, "one.json", one)
    write_payload(root, "two.json", two)
    monkeypatch.setattr(claim_parser, "MAX_CLAIMS", 3)

    # When/Then: the merged claim set fails closed before any live probes.
    with pytest.raises(ManifestValidationError, match=r"^discovery_limit$"):
        discovery.discover_and_probe(root)


def test_discovery_bounds_all_direct_directory_entries(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a directory has more direct children than the narrowed scan budget.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    for index in range(3):
        entry = root / f"unrelated-{index}.txt"
        entry.write_text("history", encoding="utf-8")
        entry.chmod(0o600)
    monkeypatch.setattr("cleanup_discovery.MAX_FILES", 2)

    # When/Then: non-JSON names cannot force unbounded enumeration work.
    with pytest.raises(ManifestValidationError, match=r"^discovery_limit$"):
        discovery.discover_and_probe(root)


def test_discovery_is_read_only(tmp_path: Path, absent_probes: Path) -> None:
    # Given: one unrelated immutable JSON file.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    entry = write_payload(root, "history.json", {"status": "passed"})
    before = (entry.read_bytes(), entry.stat().st_mtime_ns, tuple(root.iterdir()))

    # When: discovery completes.
    discovery.discover_and_probe(root)

    # Then: neither content, timestamps, nor directory membership changed.
    assert (entry.read_bytes(), entry.stat().st_mtime_ns, tuple(root.iterdir())) == before
