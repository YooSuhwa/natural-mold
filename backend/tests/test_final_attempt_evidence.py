from __future__ import annotations

# ruff: noqa: E402
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import final_attempt_evidence as evidence_module
import pytest
from final_attempt_lifecycle import (
    LifecycleError,
    abandon_final_attempt,
    reopen_seal,
    seal_attempt,
)
from operation_ledger_format import JSONValue
from plan_history_support import (
    HEAD,
    LifecycleArguments,
    _begin,
    _prepare_seal_prerequisites,
    _seal,
    _write_e2e_receipt,
    _write_failure_receipt,
    _write_final_e2e_receipt,
    _write_json,
    _write_review_receipt,
    lifecycle_arguments,
    terminal_lifecycle_arguments,
)


@pytest.fixture
def lifecycle(tmp_path: Path) -> LifecycleArguments:
    return lifecycle_arguments(tmp_path)


@pytest.mark.parametrize("mode", [0o660, 0o666])
def test_inventory_rejects_writable_regular_evidence(tmp_path: Path, mode: int) -> None:
    root = tmp_path / "attempt"
    root.mkdir(mode=0o700)
    artifact = root / "artifact.json"
    artifact.write_text("{}\n", encoding="utf-8")
    artifact.chmod(mode)

    with pytest.raises(LifecycleError, match="inventory file is unsafe"):
        evidence_module._inventory(root)


def test_abandon_requires_terminal_f2_or_f3_receipt(lifecycle: LifecycleArguments) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    receipt = _write_failure_receipt(attempt_dir, str(pointer["attempt_id"]), "F2", terminal="PASS")

    with pytest.raises(LifecycleError, match="not terminal"):
        abandon_final_attempt(
            **terminal_lifecycle_arguments(lifecycle),
            failure_receipt=receipt,
        )


@pytest.mark.parametrize("mutation", ["minimal", "unknown", "cross-kind", "empty"])
def test_abandon_rejects_forged_or_mixed_typed_failure_receipts(
    lifecycle: LifecycleArguments, mutation: str
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    receipt = _write_failure_receipt(attempt_dir, str(pointer["attempt_id"]), "F2")
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    if mutation == "minimal":
        payload = {"terminal": "FAIL", "attempt_id": pointer["attempt_id"], "head": HEAD}
    elif mutation == "unknown":
        payload["unexpected"] = True
    elif mutation == "cross-kind":
        payload["export_hashes"] = {"run-manifest": "d" * 64}
    else:
        payload["failing_nodes"] = []
    _write_json(receipt, payload)

    with pytest.raises(LifecycleError, match="schema|evidence"):
        abandon_final_attempt(
            **terminal_lifecycle_arguments(lifecycle),
            failure_receipt=receipt,
        )


@pytest.mark.parametrize("mutation", ["mismatch", "missing", "alias"])
def test_failure_receipt_rejects_unbound_artifact_hashes(
    lifecycle: LifecycleArguments, mutation: str
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    receipt = _write_failure_receipt(attempt_dir, str(pointer["attempt_id"]), "F2")
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    if mutation == "mismatch":
        payload["artifact_hashes"]["f2-static.json"]["sha256"] = "0" * 64
    elif mutation == "missing":
        payload["artifact_hashes"] = {"f2-code-review.md": {"size_bytes": 1, "sha256": "0" * 64}}
    else:
        payload["artifact_hashes"] = {
            "nested/../f2-static.json": {"size_bytes": 1, "sha256": "0" * 64}
        }
    _write_json(receipt, payload)

    with pytest.raises(LifecycleError, match="evidence"):
        abandon_final_attempt(
            **terminal_lifecycle_arguments(lifecycle),
            failure_receipt=receipt,
        )


def test_reopen_rejects_markdown_with_unbound_or_cross_gate_terminal_metadata(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    _seal(lifecycle, attempt_dir / "operations-seal.json")
    review = _write_review_receipt(attempt_dir, str(pointer["attempt_id"]), "F1")
    review.write_text(review.read_text(encoding="utf-8").replace("Gate: F1", "Gate: F4"))

    with pytest.raises(LifecycleError, match="binding"):
        reopen_seal(
            **terminal_lifecycle_arguments(lifecycle),
            failure_receipt=review,
        )


@pytest.mark.parametrize("mutation", ["mismatch", "missing", "alias"])
def test_review_receipt_rejects_unbound_gate_artifact(
    lifecycle: LifecycleArguments, mutation: str
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    _seal(lifecycle, attempt_dir / "operations-seal.json")
    review = _write_review_receipt(attempt_dir, str(pointer["attempt_id"]), "F1")
    content = review.read_text(encoding="utf-8")
    if mutation == "mismatch":
        content = content.replace(
            "Artifact-SHA256: "
            + hashlib.sha256((attempt_dir / "f1-history.json").read_bytes()).hexdigest(),
            "Artifact-SHA256: " + "0" * 64,
        )
    elif mutation == "missing":
        (attempt_dir / "f1-history.json").unlink()
    else:
        content = content.replace("Artifact: f1-history.json", "Artifact: ../f1-history.json")
    review.write_text(content, encoding="utf-8")

    with pytest.raises(LifecycleError):
        reopen_seal(
            **terminal_lifecycle_arguments(lifecycle),
            failure_receipt=review,
        )


def test_lifecycle_rejects_symlink_hardlink_and_export_namespace_attacks(
    lifecycle: LifecycleArguments, tmp_path: Path
) -> None:
    pointer = _begin(lifecycle)
    pointer_path = Path(lifecycle["pointer_path"])
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    saved_pointer = pointer_path.with_suffix(".saved")
    pointer_path.replace(saved_pointer)
    pointer_path.symlink_to(saved_pointer)
    with pytest.raises(LifecycleError, match="trusted file"):
        seal_attempt(**lifecycle, output=attempt_dir / "operations-seal.json")
    pointer_path.unlink()
    saved_pointer.replace(pointer_path)

    outside = tmp_path / "outside.txt"
    outside.write_text("safe", encoding="utf-8")
    os.link(outside, attempt_dir / "linked.txt")
    with pytest.raises(LifecycleError, match="hard-linked"):
        seal_attempt(**lifecycle, output=attempt_dir / "operations-seal.json")
    (attempt_dir / "linked.txt").unlink()
    _prepare_seal_prerequisites(attempt_dir)

    _write_json(
        attempt_dir / "f3-scripted.json",
        {
            "runner": "moldy-isolated-e2e",
            "attempt_id": pointer["attempt_id"],
            "export_directory_absolute": str(tmp_path / "outside-export"),
            "export_tree_sha256": "a" * 64,
            "export": {"export_tree_sha256": "a" * 64},
        },
    )
    with pytest.raises(LifecycleError, match="contract|namespace"):
        seal_attempt(**lifecycle, output=attempt_dir / "operations-seal.json")


@pytest.mark.parametrize("mode", [0o700, 0o755])
def test_inventory_accepts_safe_descendant_directory_modes(tmp_path: Path, mode: int) -> None:
    root = tmp_path / "inventory"
    child = root / "nested"
    child.mkdir(parents=True)
    child.chmod(mode)
    (child / "artifact.json").write_text("{}\n", encoding="utf-8")

    inventory = evidence_module._inventory(root)

    assert inventory["files"] == [
        {
            "path": "nested/artifact.json",
            "sha256": hashlib.sha256(b"{}\n").hexdigest(),
            "size": 3,
        }
    ]


@pytest.mark.parametrize("mode", [0o660, 0o666])
def test_inventory_rejects_group_or_other_writable_regular_file(tmp_path: Path, mode: int) -> None:
    root = tmp_path / "inventory"
    root.mkdir(mode=0o700)
    evidence = root / "evidence.json"
    evidence.write_text("safe", encoding="utf-8")
    evidence.chmod(mode)

    with pytest.raises(LifecycleError, match="inventory file is unsafe"):
        evidence_module._inventory(root)


@pytest.mark.parametrize("mode", [0o720, 0o702, 0o777])
def test_inventory_rejects_group_or_other_writable_descendant_directory(
    tmp_path: Path, mode: int
) -> None:
    root = tmp_path / "inventory"
    child = root / "nested"
    child.mkdir(parents=True)
    child.chmod(mode)

    with pytest.raises(LifecycleError, match="unsafe directory"):
        evidence_module._inventory(root)


def test_inventory_rejects_symlinked_descendant_directory(tmp_path: Path) -> None:
    root = tmp_path / "inventory"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "nested").symlink_to(outside, target_is_directory=True)

    with pytest.raises(LifecycleError, match="symbolic link"):
        evidence_module._inventory(root)


def test_inventory_rejects_foreign_owned_descendant_directory_portably(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "inventory"
    child = root / "nested"
    child.mkdir(parents=True)
    child_inode = child.stat().st_ino
    real_fstat = evidence_module.os.fstat

    def foreign_fstat(descriptor: int) -> os.stat_result:
        metadata = real_fstat(descriptor)
        if metadata.st_ino == child_inode:
            values = list(metadata)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return metadata

    monkeypatch.setattr(evidence_module.os, "fstat", foreign_fstat)

    with pytest.raises(LifecycleError, match="unsafe directory"):
        evidence_module._inventory(root)


def test_inventory_detects_descendant_rename_replacement_during_traversal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "inventory"
    child = root / "nested"
    child.mkdir(parents=True)
    (child / "artifact.json").write_text("{}\n", encoding="utf-8")
    displaced = root / "displaced"
    real_stat = evidence_module.os.stat
    child_stats = 0

    def replacing_stat(
        path: str | bytes | int | os.PathLike[str] | os.PathLike[bytes],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal child_stats
        if path == "nested" and dir_fd is not None and not follow_symlinks:
            child_stats += 1
            if child_stats == 2:
                child.rename(displaced)
                child.mkdir(mode=0o700)
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(evidence_module.os, "stat", replacing_stat)

    with pytest.raises(LifecycleError, match="identity changed after descent"):
        evidence_module._inventory(root)


def test_lifecycle_rejects_full_valid_export_receipt_from_arbitrary_tmp_parent(
    lifecycle: LifecycleArguments, tmp_path: Path
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    _prepare_seal_prerequisites(attempt_dir)
    _write_final_e2e_receipt(
        Path(lifecycle["repo_root"]),
        attempt_dir,
        str(pointer["attempt_id"]),
        export_parent=tmp_path / "untrusted",
    )

    with pytest.raises(LifecycleError, match="canonical validation|namespace"):
        seal_attempt(**lifecycle, output=attempt_dir / "operations-seal.json")


def _write_noncanonical_static_child(
    attempt_dir: Path,
) -> tuple[Path, bytes, dict[str, JSONValue]]:
    """Create a complete legacy static receipt and its aggregate summary."""
    child = attempt_dir / "f2-static.backend-full.0000000000000000.json"
    original = (
        b'{"status":"failed","schema_version":1,"child_exit_code":1,'
        b'"cleanup":"removed","run_root_sha256":"' + b"a" * 64 + b'"}'
    )
    child.write_bytes(original)
    return (
        child,
        original,
        {
            "relative_path": "attempt/f2-static.backend-full.0000000000000000.json",
            "sha256": hashlib.sha256(original).hexdigest(),
            "cleanup_passed": True,
            "secret_scan_passed": None,
            "workers": None,
            "retries": None,
            "screenshot_count": None,
        },
    )


def test_external_exports_ignores_referenced_noncanonical_static_child(tmp_path: Path) -> None:
    """A failed static child remains immutable evidence, not an E2E export receipt."""
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()
    child, original, summary = _write_noncanonical_static_child(attempt_dir)
    _write_json(
        attempt_dir / "f2-static.json",
        {
            "nodes": [
                {
                    "node_id": "backend-full",
                    "status": "failed",
                    "exit_code": 1,
                    "receipt": summary,
                }
            ]
        },
    )

    exports = evidence_module._external_exports(tmp_path, attempt_dir, "a" * 64)

    assert exports == []
    assert child.read_bytes() == original


@pytest.mark.parametrize("mutation", ["path", "hash", "summary"])
def test_external_exports_rejects_referenced_static_child_summary_mismatch(
    tmp_path: Path, mutation: str
) -> None:
    """A retained non-E2E child must still bind to its catalog aggregate summary."""
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()
    _child, _original, summary = _write_noncanonical_static_child(attempt_dir)
    if mutation == "path":
        summary["relative_path"] = "outside/f2-static.backend-full.0000000000000000.json"
    elif mutation == "hash":
        summary["sha256"] = "0" * 64
    else:
        summary["cleanup_passed"] = False
    _write_json(
        attempt_dir / "f2-static.json",
        {
            "nodes": [
                {
                    "node_id": "backend-full",
                    "status": "failed",
                    "exit_code": 1,
                    "receipt": summary,
                }
            ]
        },
    )

    with pytest.raises(LifecycleError, match="F2 child receipt|summary"):
        evidence_module._external_exports(tmp_path, attempt_dir, "a" * 64)


def test_external_exports_rejects_referenced_noncanonical_e2e_child(tmp_path: Path) -> None:
    """A catalog E2E child cannot evade export validation through noncanonical bytes."""
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()
    child = attempt_dir / "f2-static.scripted-full.0000000000000000.json"
    child.write_bytes(b'{"runner":"moldy-isolated-e2e"}')
    _write_json(
        attempt_dir / "f2-static.json",
        {
            "head_sha": HEAD,
            "nodes": [
                {
                    "node_id": "scripted-full",
                    "status": "failed",
                    "exit_code": 1,
                    "receipt": {
                        "relative_path": "attempt/f2-static.scripted-full.0000000000000000.json",
                        "sha256": "0" * 64,
                        "cleanup_passed": True,
                        "secret_scan_passed": True,
                        "workers": 1,
                        "retries": 0,
                        "screenshot_count": None,
                    },
                }
            ],
        },
    )

    with pytest.raises(LifecycleError, match="not canonical"):
        evidence_module._external_exports(tmp_path, attempt_dir, "a" * 64)


def test_external_exports_rejects_e2e_receipt_relabelled_as_static(tmp_path: Path) -> None:
    """A catalog static node cannot suppress a genuine E2E receipt's validation."""
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()
    child = _write_e2e_receipt(
        tmp_path,
        attempt_dir / "f2-static.backend-full.0000000000000000.json",
        "a" * 64,
        HEAD,
        "scripted-full",
        (),
        "relabelled",
        0,
    )
    content = child.read_bytes()
    _write_json(
        attempt_dir / "f2-static.json",
        {
            "nodes": [
                {
                    "node_id": "backend-full",
                    "status": "passed",
                    "exit_code": 0,
                    "receipt": {
                        "relative_path": "attempt/f2-static.backend-full.0000000000000000.json",
                        "sha256": hashlib.sha256(content).hexdigest(),
                        "cleanup_passed": True,
                        "secret_scan_passed": True,
                        "workers": 1,
                        "retries": 0,
                        "screenshot_count": 0,
                    },
                }
            ]
        },
    )

    with pytest.raises(LifecycleError, match="F2 child receipt"):
        evidence_module._external_exports(tmp_path, attempt_dir, "a" * 64)


def test_external_exports_rejects_unreferenced_f2_e2e_receipt(tmp_path: Path) -> None:
    """An F2 E2E child cannot exist outside the aggregate's exact child set."""
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()
    _write_e2e_receipt(
        tmp_path,
        attempt_dir / "f2-static.scripted-full.0000000000000000.json",
        "a" * 64,
        HEAD,
        "scripted-full",
        (),
        "unreferenced",
        0,
    )
    _write_json(attempt_dir / "f2-static.json", {"nodes": []})

    with pytest.raises(LifecycleError, match="F2 child receipts"):
        evidence_module._external_exports(tmp_path, attempt_dir, "a" * 64)


def test_external_exports_rejects_expected_e2e_receipt_with_wrong_runner(tmp_path: Path) -> None:
    """A named F3 receipt must be an E2E receipt instead of silently disappearing."""
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()
    _write_json(attempt_dir / "f3-scripted.json", {"runner": "wrong-runner"})

    with pytest.raises(LifecycleError, match="runner"):
        evidence_module._external_exports(tmp_path, attempt_dir, "a" * 64)


def test_lifecycle_seals_exact_direct_child_final_attempt_export(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    repo_root = Path(lifecycle["repo_root"])
    attempt_dir = repo_root / str(pointer["attempt_dir"])
    _prepare_seal_prerequisites(attempt_dir)
    _write_final_e2e_receipt(repo_root, attempt_dir, str(pointer["attempt_id"]))

    sealed = seal_attempt(**lifecycle, output=attempt_dir / "operations-seal.json")

    assert sealed["status"] == "sealed"
    seal = json.loads((attempt_dir / "operations-seal.json").read_bytes())
    assert all(
        len(seal[name]) == 64
        for name in (
            "prerequisite_attestation_sha256",
            "attempt_inventory_sha256",
            "external_exports_sha256",
        )
    )
    journals = list(Path(lifecycle["journal_root"]).glob("seal-*.json"))
    assert len(journals) == 1
    assert json.loads(journals[0].read_bytes())["prerequisite_attestation"]["attempt_id"] == str(
        pointer["attempt_id"]
    )


def test_seal_rejects_shape_only_prerequisites(lifecycle: LifecycleArguments) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    _prepare_seal_prerequisites(attempt_dir)
    _write_json(attempt_dir / "f2-static.json", {"status": "passed"})

    with pytest.raises(LifecycleError, match="F2 aggregate"):
        seal_attempt(**lifecycle, output=attempt_dir / "operations-seal.json")


@pytest.mark.parametrize(
    ("name", "old", "new", "reason"),
    [
        ("f2-code-review.md", "Role: code", "Role: security", "review footer"),
        ("f2-security-review.md", "Terminal: APPROVE", "Terminal: PASS", "review footer"),
        ("f3-manual-qa.md", "View-Count: 24", "View-Count: 23", "manual QA footer"),
    ],
)
def test_seal_rejects_exact_review_footer_mutations(
    lifecycle: LifecycleArguments, name: str, old: str, new: str, reason: str
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    _prepare_seal_prerequisites(attempt_dir)
    receipt = attempt_dir / name
    receipt.write_text(receipt.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    with pytest.raises(LifecycleError, match=reason):
        seal_attempt(**lifecycle, output=attempt_dir / "operations-seal.json")


def test_seal_rejects_unreferenced_f2_child_and_final_skips(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    _prepare_seal_prerequisites(attempt_dir)
    _write_json(
        attempt_dir / "f2-static.backend-full.0000000000000000.json",
        {
            "schema_version": 1,
            "status": "passed",
            "child_exit_code": 0,
            "cleanup": "removed",
            "run_root_sha256": "a" * 64,
        },
    )

    with pytest.raises(
        LifecycleError,
        match="exact prerequisite set|exactly match aggregate references",
    ):
        seal_attempt(**lifecycle, output=attempt_dir / "operations-seal.json")

    (attempt_dir / "f2-static.backend-full.0000000000000000.json").unlink()
    scripted = attempt_dir / "f3-scripted.json"
    payload = json.loads(scripted.read_bytes())
    payload["skipped_ids"] = ["scripted-full::e2e/skipped.spec.ts::skipped"]
    _write_json(scripted, payload)
    with pytest.raises(LifecycleError, match="F3 final receipt"):
        seal_attempt(**lifecycle, output=attempt_dir / "operations-seal.json")


def test_lifecycle_rejects_unlisted_secret_file_in_retained_export(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    repo_root = Path(lifecycle["repo_root"])
    attempt_dir = repo_root / str(pointer["attempt_dir"])
    _prepare_seal_prerequisites(attempt_dir)
    receipt_path = _write_final_e2e_receipt(repo_root, attempt_dir, str(pointer["attempt_id"]))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    export_dir = Path(receipt["export_directory_absolute"])
    (export_dir / "unlisted-secret.txt").write_text(
        "password=clearly-dummy-not-a-real-secret", encoding="utf-8"
    )

    with pytest.raises(LifecycleError, match="canonical validation|omits or duplicates"):
        seal_attempt(**lifecycle, output=attempt_dir / "operations-seal.json")
