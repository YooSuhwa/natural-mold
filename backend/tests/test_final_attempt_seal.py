"""Focused strict prerequisite-attestation and post-seal mutation contracts."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from final_attempt_evidence import _external_exports, _inventory  # noqa: E402
from final_attempt_io import _digest_json  # noqa: E402
from final_attempt_lifecycle import (  # noqa: E402
    LifecycleError,
    _attestation_hashes,
    seal_attempt,
    validate_seal,
)
from final_attempt_review_contract import prerequisite_attestation  # noqa: E402
from operation_ledger_format import canonical_line  # noqa: E402
from plan_history_support import (  # noqa: E402
    HEAD,
    PLAN_SHA,
    REVIEW_ROUND,
    _begin,
    _prepare_seal_prerequisites,
    _seal,
    _write_json,
    lifecycle_arguments,
)


@pytest.fixture
def lifecycle(tmp_path: Path) -> dict[str, object]:
    return lifecycle_arguments(tmp_path)


def test_validate_seal_rejects_prerequisite_mutation_but_allows_exact_later_outputs(
    lifecycle: dict[str, object],
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    output = attempt_dir / "operations-seal.json"
    _seal(lifecycle, output)
    arguments = {
        "contract": lifecycle["contract"],
        "operations": Path(lifecycle["operations"]),
        "seal_path": output,
        "pointer_path": Path(lifecycle["pointer_path"]),
        "expected_head": HEAD,
        "expected_attempt_id": str(pointer["attempt_id"]),
        "expected_plan_sha": PLAN_SHA,
        "expected_review_round": REVIEW_ROUND,
    }
    for name in ("f1-history.json", "f1-review.md", "f4-scope.json", "f4-review.md"):
        (attempt_dir / name).write_text("later gate output\n", encoding="utf-8")

    assert validate_seal(**arguments)["attempt_id"] == pointer["attempt_id"]

    (attempt_dir / "unreviewed.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(LifecycleError, match="exact prerequisite set"):
        validate_seal(**arguments)
    (attempt_dir / "unreviewed.json").unlink()
    (attempt_dir / "nested").mkdir()
    with pytest.raises(LifecycleError, match="nested directory"):
        validate_seal(**arguments)
    (attempt_dir / "nested").rmdir()
    review = attempt_dir / "f2-code-review.md"
    review.write_bytes(review.read_bytes().replace(b"No blocking", b"One blocking"))
    with pytest.raises(LifecycleError, match="binding|attestation"):
        validate_seal(**arguments)


def test_seal_detects_prerequisite_mutation_between_durable_phases(
    lifecycle: dict[str, object],
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    output = attempt_dir / "operations-seal.json"
    _prepare_seal_prerequisites(attempt_dir)

    def mutate_after_seal_write(boundary: str) -> None:
        if boundary == "seal:file:parent_fsync":
            review = attempt_dir / "f2-security-review.md"
            review.write_bytes(review.read_bytes().replace(b"No blocking", b"One blocking"))

    with pytest.raises(LifecycleError, match="binding|attestation"):
        seal_attempt(**lifecycle, output=output, hook=mutate_after_seal_write)


def test_seal_rejects_empty_nested_attempt_directory(lifecycle: dict[str, object]) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    _prepare_seal_prerequisites(attempt_dir)
    (attempt_dir / "empty").mkdir()

    with pytest.raises(LifecycleError, match="nested directory"):
        seal_attempt(**lifecycle, output=attempt_dir / "operations-seal.json")


def test_seal_recovery_rejects_self_consistent_forged_base_against_tracked_contract(
    lifecycle: dict[str, object],
) -> None:
    pointer = _begin(lifecycle)
    repo_root = Path(lifecycle["repo_root"])
    attempt_dir = repo_root / str(pointer["attempt_dir"])
    output = attempt_dir / "operations-seal.json"
    _prepare_seal_prerequisites(attempt_dir)

    def crash_after_prepared(boundary: str) -> None:
        if boundary == "journal:prepared:parent_fsync":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        seal_attempt(**lifecycle, output=output, hook=crash_after_prepared)

    aggregate_path = attempt_dir / "f2-static.json"
    original_aggregate = aggregate_path.read_bytes()
    aggregate = json.loads(original_aggregate)
    aggregate["base_sha"] = "0" * 40
    aggregate_path.write_bytes(canonical_line(aggregate))
    replacement_hash = hashlib.sha256(aggregate_path.read_bytes()).hexdigest().encode()
    original_hash = hashlib.sha256(original_aggregate).hexdigest().encode()
    for name in ("f2-code-review.md", "f2-security-review.md"):
        review = attempt_dir / name
        review.write_bytes(review.read_bytes().replace(original_hash, replacement_hash))

    attempt_id = str(pointer["attempt_id"])
    inventory = _inventory(attempt_dir)
    exports = _external_exports(repo_root, attempt_dir, attempt_id)
    attestation = prerequisite_attestation(
        repo_root,
        attempt_dir,
        attempt_id,
        str(pointer["head"]),
        "0" * 40,
        inventory,
        exports,
    )
    attestation_sha, inventory_sha, exports_sha = _attestation_hashes(attestation)
    journal_path = next(Path(lifecycle["journal_root"]).glob("seal-*.json"))
    journal = json.loads(journal_path.read_bytes())
    inputs = journal["inputs"]
    inputs["prerequisite_attestation_sha256"] = attestation_sha
    inputs["attempt_inventory_sha256"] = inventory_sha
    inputs["external_exports_sha256"] = exports_sha
    transition_id = _digest_json(inputs)
    journal["transition_id"] = transition_id
    journal["attempt_inventory"] = inventory
    journal["external_exports"] = exports
    journal["prerequisite_attestation"] = attestation
    forged_path = journal_path.with_name(f"seal-{transition_id}.json")
    _write_json(forged_path, journal)
    journal_path.unlink()

    with pytest.raises(LifecycleError, match="base disagrees with tracked contract"):
        seal_attempt(**lifecycle, output=output)
