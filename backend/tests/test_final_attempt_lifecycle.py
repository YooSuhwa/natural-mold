from __future__ import annotations

# ruff: noqa: E402
import hashlib
import json
import multiprocessing
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import final_attempt_io as io_module
import operation_ledger_writer as writer_module
import plan_history_support as support_module
import pytest
from final_attempt_authority import (
    FinalAttemptAuthorityError,
    verify_open_final_attempt_authority,
)
from final_attempt_lifecycle import (
    LifecycleError,
    abandon_final_attempt,
    begin_final_attempt,
    reopen_seal,
    seal_attempt,
    validate_seal,
)
from operation_ledger_chain import _verify_bytes
from operation_ledger_format import JSONValue, LedgerError, _with_hash, canonical_line
from operation_ledger_writer import (
    EvidenceWriterLock,
    append_operation,
    append_operation_locked,
    evidence_writer_lock,
)
from plan_history_contract import load_verified_operations
from plan_history_support import (
    EVIDENCE,
    HEAD,
    PLAN_SHA,
    REVIEW_ROUND,
    LifecycleArguments,
    _begin,
    _cross_process_append,
    _seal,
    _write_failure_receipt,
    _write_json,
    _write_review_receipt,
    copy_isolated_operations,
    lifecycle_arguments,
    terminal_lifecycle_arguments,
)


@pytest.fixture
def lifecycle(tmp_path: Path) -> LifecycleArguments:
    return lifecycle_arguments(tmp_path)


def test_active_attempt_pointer_deletion_blocks_public_append(tmp_path: Path) -> None:
    arguments = lifecycle_arguments(tmp_path)
    begin_final_attempt(**arguments)
    Path(arguments["pointer_path"]).unlink()
    operations = Path(arguments["operations"])
    before = operations.read_bytes()

    with pytest.raises(LedgerError, match="pointer is missing"):
        append_operation(
            operations,
            task_id="pointer-deletion",
            action_class="verification",
            arguments={},
            status="passed",
        )

    assert operations.read_bytes() == before


def test_lifecycle_fixture_excludes_open_source_attempt_without_mutating_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_evidence = tmp_path / "source/.omo/evidence/project-restart-consolidated-roadmap"
    source_evidence.mkdir(parents=True)
    source_operations = source_evidence / "operations.ndjson"
    source_operations.write_bytes((EVIDENCE / "operations.ndjson").read_bytes())
    attempt_id = "a" * 64
    _append_forged_lifecycle_entry(
        source_operations,
        action_class="final_attempt_started",
        arguments={"attempt_id": attempt_id, "head": HEAD},
    )
    source_entries = load_verified_operations(source_operations)
    start_index = len(source_entries) - 1
    _write_json(
        source_evidence / "current-final-attempt.json",
        {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "attempt_dir": (
                f".omo/evidence/project-restart-consolidated-roadmap/final-attempts/{attempt_id}"
            ),
            "status": "open",
            "head": HEAD,
        },
    )
    source_before = source_operations.read_bytes()
    source_hash = hashlib.sha256(source_before).hexdigest()
    expected = b"".join(source_before.splitlines(keepends=True)[:start_index])
    monkeypatch.setattr(support_module, "EVIDENCE", source_evidence)

    isolated = lifecycle_arguments(tmp_path / "isolated")

    copied = Path(isolated["operations"])
    assert copied.read_bytes() == expected
    assert load_verified_operations(copied) == source_entries[:start_index]
    assert source_operations.read_bytes() == source_before
    assert hashlib.sha256(source_operations.read_bytes()).hexdigest() == source_hash


def test_lifecycle_fixture_rejects_stale_open_source_pointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_evidence = tmp_path / "source/.omo/evidence/project-restart-consolidated-roadmap"
    source_evidence.mkdir(parents=True)
    source_operations = source_evidence / "operations.ndjson"
    source_operations.write_bytes((EVIDENCE / "operations.ndjson").read_bytes())
    entries = load_verified_operations(source_operations)
    historic_start = next(
        entry
        for entry in entries
        if entry["action_class"] == "final_attempt_started" and entry["status"] == "passed"
    )
    arguments = historic_start["arguments"]
    assert isinstance(arguments, dict)
    attempt_id = arguments["attempt_id"]
    head = arguments["head"]
    assert isinstance(attempt_id, str)
    assert isinstance(head, str)
    _write_json(
        source_evidence / "current-final-attempt.json",
        {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "attempt_dir": (
                f".omo/evidence/project-restart-consolidated-roadmap/final-attempts/{attempt_id}"
            ),
            "status": "open",
            "head": head,
        },
    )
    destination = (
        tmp_path / "isolated/.omo/evidence/project-restart-consolidated-roadmap/operations.ndjson"
    )
    destination.parent.mkdir(parents=True)
    monkeypatch.setattr(support_module, "EVIDENCE", source_evidence)

    with pytest.raises(LedgerError, match="active lifecycle"):
        support_module.copy_isolated_operations(destination)

    assert not destination.exists()


def test_lifecycle_fixture_rejects_open_source_pointer_with_extra_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_evidence = tmp_path / "source/.omo/evidence/project-restart-consolidated-roadmap"
    source_evidence.mkdir(parents=True)
    source_operations = source_evidence / "operations.ndjson"
    source_operations.write_bytes((EVIDENCE / "operations.ndjson").read_bytes())
    attempt_id = "b" * 64
    _append_forged_lifecycle_entry(
        source_operations,
        action_class="final_attempt_started",
        arguments={"attempt_id": attempt_id, "head": HEAD},
    )
    _write_json(
        source_evidence / "current-final-attempt.json",
        {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "attempt_dir": (
                f".omo/evidence/project-restart-consolidated-roadmap/final-attempts/{attempt_id}"
            ),
            "status": "open",
            "head": HEAD,
            "unexpected": True,
        },
    )
    destination = (
        tmp_path / "isolated/.omo/evidence/project-restart-consolidated-roadmap/operations.ndjson"
    )
    destination.parent.mkdir(parents=True)
    monkeypatch.setattr(support_module, "EVIDENCE", source_evidence)

    with pytest.raises(LedgerError, match="pointer schema"):
        support_module.copy_isolated_operations(destination)

    assert not destination.exists()


def test_begin_creates_one_open_hash_named_empty_attempt(lifecycle: LifecycleArguments) -> None:
    pointer = _begin(lifecycle)

    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    assert pointer["status"] == "open"
    assert len(str(pointer["attempt_id"])) == 64
    assert list(attempt_dir.iterdir()) == []
    with pytest.raises(LifecycleError, match="cannot begin while pointer is open"):
        _begin(lifecycle)


def test_begin_rejects_attempt_id_collision(lifecycle: LifecycleArguments) -> None:
    _begin(lifecycle)
    Path(lifecycle["pointer_path"]).unlink()
    for journal in Path(lifecycle["journal_root"]).glob("*.json"):
        journal.unlink()
    copy_isolated_operations(Path(lifecycle["operations"]))

    with pytest.raises(LifecycleError, match="collides"):
        _begin(lifecycle)


def test_abandon_freezes_attempt_and_rejects_repeat_or_changed_inventory(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    receipt = _write_failure_receipt(attempt_dir, str(pointer["attempt_id"]), "F3")
    arguments = terminal_lifecycle_arguments(lifecycle)

    abandoned = abandon_final_attempt(**arguments, failure_receipt=receipt)

    assert abandoned["status"] == "abandoned"
    with pytest.raises(LifecycleError, match="requires a open attempt"):
        abandon_final_attempt(**arguments, failure_receipt=receipt)
    receipt.write_bytes(receipt.read_bytes() + b"\n")
    with pytest.raises(LifecycleError, match="inventory changed"):
        _begin(lifecycle)


def test_seal_blocks_append_and_detects_wrong_head_or_mutation(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    output = attempt_dir / "operations-seal.json"
    sealed = _seal(lifecycle, output)

    assert sealed["status"] == "sealed"
    with pytest.raises(LedgerError, match="externally sealed"):
        append_operation(
            Path(lifecycle["operations"]),
            task_id="fixture",
            action_class="mutation",
            arguments={},
            status="passed",
        )


def test_cross_process_append_cannot_commit_across_seal(lifecycle: LifecycleArguments) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    output = attempt_dir / "operations-seal.json"
    context = multiprocessing.get_context("spawn")
    queue: multiprocessing.Queue[str] = context.Queue()
    process = context.Process(
        target=_cross_process_append,
        args=(str(lifecycle["operations"]), queue),
    )

    def start_append(boundary: str) -> None:
        if boundary == "journal:prepared:file_fsync":
            process.start()
            assert queue.get(timeout=10) == "started"

    sealed = _seal(lifecycle, output, hook=start_append)
    process.join(timeout=15)

    assert sealed["status"] == "sealed"
    assert process.exitcode == 0
    assert queue.get(timeout=5) == "rejected"
    with pytest.raises(LifecycleError, match="another HEAD"):
        validate_seal(
            contract=lifecycle["contract"],
            operations=Path(lifecycle["operations"]),
            seal_path=output,
            pointer_path=Path(lifecycle["pointer_path"]),
            expected_head="e" * 40,
            expected_attempt_id=str(pointer["attempt_id"]),
            expected_plan_sha=PLAN_SHA,
            expected_review_round=REVIEW_ROUND,
        )
    original_seal = output.read_bytes()
    forged = json.loads(original_seal)
    forged["extra"] = True
    _write_json(output, forged)
    with pytest.raises(LifecycleError, match="schema"):
        validate_seal(
            contract=lifecycle["contract"],
            operations=Path(lifecycle["operations"]),
            seal_path=output,
            pointer_path=Path(lifecycle["pointer_path"]),
            expected_head=HEAD,
            expected_attempt_id=str(pointer["attempt_id"]),
            expected_plan_sha=PLAN_SHA,
            expected_review_round=REVIEW_ROUND,
        )
    output.write_bytes(original_seal)
    forged = json.loads(original_seal)
    forged["seal_id"] = "0" * 64
    _write_json(output, forged)
    with pytest.raises(LifecycleError, match="identity"):
        validate_seal(
            contract=lifecycle["contract"],
            operations=Path(lifecycle["operations"]),
            seal_path=output,
            pointer_path=Path(lifecycle["pointer_path"]),
            expected_head=HEAD,
            expected_attempt_id=str(pointer["attempt_id"]),
            expected_plan_sha=PLAN_SHA,
            expected_review_round=REVIEW_ROUND,
        )
    output.write_bytes(original_seal)
    with Path(lifecycle["operations"]).open("ab") as ledger:
        ledger.write(b"post-seal-mutation")
    with pytest.raises(LifecycleError, match="mutated after seal"):
        validate_seal(
            contract=lifecycle["contract"],
            operations=Path(lifecycle["operations"]),
            seal_path=output,
            pointer_path=Path(lifecycle["pointer_path"]),
            expected_head=HEAD,
            expected_attempt_id=str(pointer["attempt_id"]),
            expected_plan_sha=PLAN_SHA,
            expected_review_round=REVIEW_ROUND,
        )


def test_reopen_requires_terminal_f1_or_f4_and_allows_new_attempt(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    output = attempt_dir / "operations-seal.json"
    _seal(lifecycle, output)
    review = _write_review_receipt(attempt_dir, str(pointer["attempt_id"]), "F1")
    arguments = terminal_lifecycle_arguments(lifecycle)

    reopened = reopen_seal(**arguments, failure_receipt=review)

    assert reopened["status"] == "reopened"
    with pytest.raises(LifecycleError, match="requires a sealed attempt"):
        reopen_seal(**arguments, failure_receipt=review)
    next_attempt = _begin(lifecycle)
    assert next_attempt["attempt_id"] != pointer["attempt_id"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt_id", "short"),
        ("head", "not-a-head"),
        ("attempt_dir", "../final-attempts/escape"),
        ("status", "unknown"),
    ],
)
def test_ledger_writer_rejects_malformed_external_pointer_values(
    lifecycle: LifecycleArguments, field: str, value: str
) -> None:
    pointer_path = Path(lifecycle["pointer_path"])
    pointer: dict[str, JSONValue] = {
        "schema_version": 1,
        "attempt_id": "a" * 64,
        "attempt_dir": ".omo/evidence/project-restart-consolidated-roadmap/final-attempts/"
        + "a" * 64,
        "status": "open",
        "head": HEAD,
    }
    pointer[field] = value
    _write_json(pointer_path, pointer)

    with pytest.raises(LedgerError, match="pointer"):
        append_operation(
            Path(lifecycle["operations"]),
            task_id="fixture",
            action_class="mutation",
            arguments={},
            status="passed",
        )


def test_ledger_append_allows_initial_absent_final_attempt_pointer(
    lifecycle: LifecycleArguments,
) -> None:
    append_operation(
        Path(lifecycle["operations"]),
        task_id="pre-final-attempt",
        action_class="verification",
        arguments={},
        status="passed",
    )


def test_ledger_append_rejects_deleted_pointer_for_active_attempt(
    lifecycle: LifecycleArguments,
) -> None:
    _begin(lifecycle)
    pointer = Path(lifecycle["pointer_path"])
    pointer.unlink()
    operations = Path(lifecycle["operations"])
    before = operations.read_bytes()

    with pytest.raises(LedgerError, match="pointer is missing"):
        append_operation(
            operations,
            task_id="post-pointer-deletion",
            action_class="verification",
            arguments={},
            status="passed",
        )

    assert operations.read_bytes() == before


def test_ledger_append_allows_deleted_pointer_after_committed_abandon(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    receipt = _write_failure_receipt(attempt_dir, str(pointer["attempt_id"]), "F2")
    arguments = terminal_lifecycle_arguments(lifecycle)
    abandon_final_attempt(**arguments, failure_receipt=receipt)
    Path(lifecycle["pointer_path"]).unlink()

    append_operation(
        Path(lifecycle["operations"]),
        task_id="after-abandon",
        action_class="verification",
        arguments={},
        status="passed",
    )


def test_ledger_append_allows_deleted_pointer_after_committed_reopen(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    _seal(lifecycle, attempt_dir / "operations-seal.json")
    review = _write_review_receipt(attempt_dir, str(pointer["attempt_id"]), "F1")
    arguments = terminal_lifecycle_arguments(lifecycle)
    reopen_seal(**arguments, failure_receipt=review)
    Path(lifecycle["pointer_path"]).unlink()

    append_operation(
        Path(lifecycle["operations"]),
        task_id="after-reopen",
        action_class="verification",
        arguments={},
        status="passed",
    )


def test_deleted_sealed_pointer_blocks_public_append_without_mutating_ledger(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    _seal(lifecycle, attempt_dir / "operations-seal.json")
    Path(lifecycle["pointer_path"]).unlink()
    operations = Path(lifecycle["operations"])
    before = operations.read_bytes()

    with pytest.raises(LedgerError, match="pointer is missing"):
        append_operation(
            operations,
            task_id="sealed-pointer-deletion",
            action_class="verification",
            arguments={},
            status="passed",
        )

    assert operations.read_bytes() == before


def test_pointer_deletion_rejects_duplicate_lifecycle_start_relation(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    attempt_id = str(pointer["attempt_id"])
    operations = Path(lifecycle["operations"])
    _append_forged_lifecycle_entry(
        operations,
        action_class="final_attempt_abandoned",
        arguments={
            "abandon_id": "a" * 64,
            "attempt_id": attempt_id,
            "failure_path": "f2-failure.json",
            "failure_sha256": "b" * 64,
        },
    )
    _append_forged_lifecycle_entry(
        operations,
        action_class="final_attempt_started",
        arguments={"attempt_id": attempt_id, "head": HEAD},
    )
    Path(lifecycle["pointer_path"]).unlink()
    before = operations.read_bytes()

    with pytest.raises(LedgerError, match="duplicate"):
        append_operation(
            operations,
            task_id="duplicate-relation",
            action_class="verification",
            arguments={},
            status="passed",
        )

    assert operations.read_bytes() == before


def test_intact_pointer_rejects_duplicate_lifecycle_start_relation(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    attempt_id = str(pointer["attempt_id"])
    operations = Path(lifecycle["operations"])
    _append_forged_lifecycle_entry(
        operations,
        action_class="final_attempt_abandoned",
        arguments={
            "abandon_id": "a" * 64,
            "attempt_id": attempt_id,
            "failure_path": "f2-failure.json",
            "failure_sha256": "b" * 64,
        },
    )
    _append_forged_lifecycle_entry(
        operations,
        action_class="final_attempt_started",
        arguments={"attempt_id": attempt_id, "head": HEAD},
    )
    before = operations.read_bytes()

    with pytest.raises(LedgerError, match="duplicate"):
        append_operation(
            operations,
            task_id="duplicate-relation-intact-pointer",
            action_class="verification",
            arguments={},
            status="passed",
        )

    assert operations.read_bytes() == before


def test_intact_pointer_rejects_lifecycle_closer_for_another_attempt(
    lifecycle: LifecycleArguments,
) -> None:
    _begin(lifecycle)
    operations = Path(lifecycle["operations"])
    _append_forged_lifecycle_entry(
        operations,
        action_class="final_attempt_abandoned",
        arguments={
            "abandon_id": "a" * 64,
            "attempt_id": "b" * 64,
            "failure_path": "f2-failure.json",
            "failure_sha256": "c" * 64,
        },
    )
    before = operations.read_bytes()

    with pytest.raises(LedgerError, match="lifecycle relation"):
        append_operation(
            operations,
            task_id="wrong-closer",
            action_class="verification",
            arguments={},
            status="passed",
        )

    assert operations.read_bytes() == before


def test_missing_pointer_rejects_malformed_lifecycle_event_before_mutation(
    lifecycle: LifecycleArguments,
) -> None:
    operations = Path(lifecycle["operations"])
    _append_forged_lifecycle_entry(
        operations,
        action_class="final_attempt_started",
        arguments={"attempt_id": "a" * 64, "head": HEAD},
        status="failed",
    )
    before = operations.read_bytes()

    with pytest.raises(LedgerError, match="lifecycle relation"):
        append_operation(
            operations,
            task_id="malformed-lifecycle",
            action_class="verification",
            arguments={},
            status="passed",
        )

    assert operations.read_bytes() == before


def test_nonterminal_journal_blocks_public_append_but_exact_recovery_appends_once(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    receipt = _write_failure_receipt(attempt_dir, str(pointer["attempt_id"]), "F2")
    arguments = terminal_lifecycle_arguments(lifecycle)

    def crash(boundary: str) -> None:
        if boundary == "journal:prepared:parent_fsync":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        abandon_final_attempt(**arguments, failure_receipt=receipt, hook=crash)

    operations = Path(lifecycle["operations"])
    before = operations.read_bytes()
    with pytest.raises(LedgerError, match="nonterminal"):
        append_operation(
            operations,
            task_id="public-during-recovery",
            action_class="verification",
            arguments={},
            status="passed",
        )
    assert operations.read_bytes() == before

    journal = json.loads(next(Path(lifecycle["journal_root"]).glob("abandon-*.json")).read_bytes())
    action_class, exact_arguments = writer_module._expected_journal_event(journal)
    unchecked_append = cast(Callable[..., object], append_operation_locked)
    with evidence_writer_lock(operations) as lock, pytest.raises(TypeError):
        unchecked_append(
            operations,
            evidence_lock=lock,
            task_id="forged-private-transition",
            action_class=action_class,
            arguments=exact_arguments,
            status="passed",
            _permit_lifecycle_transition=True,
        )
    assert operations.read_bytes() == before

    assert not hasattr(writer_module, "append_final_attempt_lifecycle_operation")
    with evidence_writer_lock(operations) as lock, pytest.raises(TypeError):
        unchecked_append(
            operations,
            evidence_lock=lock,
            task_id="forged-replacement-transition",
            action_class=action_class,
            arguments=exact_arguments,
            status="passed",
            lifecycle_revalidation=lambda: None,
        )
    assert operations.read_bytes() == before

    abandoned = abandon_final_attempt(**arguments, failure_receipt=receipt)
    events = [
        entry
        for entry in _verify_bytes(operations.read_bytes())
        if entry["action_class"] == "final_attempt_abandoned"
        and isinstance(entry["arguments"], dict)
        and entry["arguments"].get("attempt_id") == pointer["attempt_id"]
    ]

    assert abandoned["status"] == "abandoned"
    assert len(events) == 1


@pytest.mark.parametrize("mode", [0o660, 0o666])
def test_ledger_append_rejects_writable_pointer_before_mutation(
    lifecycle: LifecycleArguments, mode: int
) -> None:
    _begin(lifecycle)
    pointer = Path(lifecycle["pointer_path"])
    pointer.chmod(mode)
    operations = Path(lifecycle["operations"])
    before = operations.read_bytes()

    with pytest.raises(LedgerError, match="pointer"):
        append_operation(
            operations,
            task_id="writable-pointer",
            action_class="verification",
            arguments={},
            status="passed",
        )

    assert operations.read_bytes() == before


@pytest.mark.parametrize("mode", [0o660, 0o666])
def test_ledger_append_rejects_writable_writer_lock_before_mutation(
    lifecycle: LifecycleArguments, mode: int
) -> None:
    operations = Path(lifecycle["operations"])
    writer_lock = operations.parent / ".evidence-writer.lock"
    writer_lock.write_bytes(b"")
    writer_lock.chmod(mode)
    before = operations.read_bytes()

    with pytest.raises(LedgerError, match="writer lock"):
        append_operation(
            operations,
            task_id="writable-writer-lock",
            action_class="verification",
            arguments={},
            status="passed",
        )

    assert operations.read_bytes() == before


@pytest.mark.parametrize("mode", [0o660, 0o666])
def test_ledger_append_rejects_writable_committed_journal_before_mutation(
    lifecycle: LifecycleArguments, mode: int
) -> None:
    _begin(lifecycle)
    journal = next(Path(lifecycle["journal_root"]).glob("begin-*.json"))
    journal.chmod(mode)
    operations = Path(lifecycle["operations"])
    before = operations.read_bytes()

    with pytest.raises(LedgerError, match="journal"):
        append_operation(
            operations,
            task_id="writable-journal",
            action_class="verification",
            arguments={},
            status="passed",
        )

    assert operations.read_bytes() == before


def test_ledger_append_detects_pointer_replacement_during_descriptor_read(
    lifecycle: LifecycleArguments, monkeypatch: pytest.MonkeyPatch
) -> None:
    _begin(lifecycle)
    pointer = Path(lifecycle["pointer_path"])
    replacement = pointer.with_name("replacement-pointer.json")
    replacement.write_bytes(pointer.read_bytes())
    replacement.chmod(0o600)
    original_read = writer_module.os.read
    replaced = False

    def replace_pointer(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        result = original_read(descriptor, size)
        if not replaced and os.fstat(descriptor).st_ino == pointer.stat().st_ino:
            replaced = True
            replacement.replace(pointer)
        return result

    monkeypatch.setattr(writer_module.os, "read", replace_pointer)
    operations = Path(lifecycle["operations"])
    before = operations.read_bytes()

    with pytest.raises(LedgerError, match="pointer identity changed"):
        append_operation(
            operations,
            task_id="pointer-replacement",
            action_class="verification",
            arguments={},
            status="passed",
        )

    assert replaced
    assert operations.read_bytes() == before


def test_ledger_append_detects_journal_replacement_during_descriptor_read(
    lifecycle: LifecycleArguments, monkeypatch: pytest.MonkeyPatch
) -> None:
    _begin(lifecycle)
    journal = next(Path(lifecycle["journal_root"]).glob("begin-*.json"))
    replacement = journal.with_name("replacement-journal.json")
    replacement.write_bytes(journal.read_bytes())
    replacement.chmod(0o600)
    original_read = writer_module.os.read
    replaced = False

    def replace_journal(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        result = original_read(descriptor, size)
        if not replaced and os.fstat(descriptor).st_ino == journal.stat().st_ino:
            replaced = True
            replacement.replace(journal)
        return result

    monkeypatch.setattr(writer_module.os, "read", replace_journal)
    operations = Path(lifecycle["operations"])
    before = operations.read_bytes()

    with pytest.raises(LedgerError, match="journal identity changed"):
        append_operation(
            operations,
            task_id="journal-replacement",
            action_class="verification",
            arguments={},
            status="passed",
        )

    assert replaced
    assert operations.read_bytes() == before


def test_ledger_append_detects_writer_lock_replacement_before_commit(
    lifecycle: LifecycleArguments,
) -> None:
    operations = Path(lifecycle["operations"])
    append_operation(
        operations,
        task_id="establish-writer-lock",
        action_class="verification",
        arguments={},
        status="passed",
    )
    writer_lock = operations.parent / ".evidence-writer.lock"
    replacement = writer_lock.with_name("replacement-writer-lock")
    replacement.write_bytes(b"")
    replacement.chmod(0o600)
    before = operations.read_bytes()

    def replace_lock(boundary: str) -> None:
        if boundary == "before_commit":
            replacement.replace(writer_lock)

    with pytest.raises(LedgerError, match="writer lock identity"):
        append_operation(
            operations,
            task_id="writer-lock-replacement",
            action_class="verification",
            arguments={},
            status="passed",
            filesystem_hook=replace_lock,
        )

    assert operations.read_bytes() == before


def _append_forged_lifecycle_entry(
    operations: Path,
    *,
    action_class: str,
    arguments: dict[str, JSONValue],
    status: str = "passed",
) -> None:
    """Append a hash-valid historic entry to exercise fail-closed read recovery."""
    existing = operations.read_bytes()
    entries = _verify_bytes(existing)
    previous_hash = entries[-1]["entry_hash"]
    assert isinstance(previous_hash, str)
    entry = _with_hash(
        {
            "schema_version": 1,
            "sequence": len(entries),
            "previous_entry_hash": previous_hash,
            "timestamp_utc": "2026-09-05T00:00:00Z",
            "task_id": "final-attempt",
            "action_class": action_class,
            "arguments": arguments,
            "status": status,
        }
    )
    operations.write_bytes(existing + canonical_line(entry))


def test_seal_journal_shape_accepts_exact_prerequisite_attestation() -> None:
    inventory: dict[str, JSONValue] = {
        "files": [],
        "sha256": hashlib.sha256(canonical_line({"files": []})).hexdigest(),
    }
    attestation: dict[str, JSONValue] = {
        "schema_version": 1,
        "attempt_id": "a" * 64,
        "head": HEAD,
        "base_sha": "b" * 40,
        "receipts": [],
        "attempt_inventory": inventory,
        "external_exports": [],
    }
    journal: dict[str, JSONValue] = {
        "schema_version": 1,
        "transition": "seal",
        "transition_id": "c" * 64,
        "phase": "committed",
        "inputs": {
            "prerequisite_attestation_sha256": hashlib.sha256(
                canonical_line(attestation)
            ).hexdigest()
        },
        "pointer_sha256": "d" * 64,
        "operations_sha256": "e" * 64,
        "attempt_inventory": inventory,
        "external_exports": [],
        "prerequisite_attestation": attestation,
    }

    writer_module._validate_journal_shape(journal)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda journal: journal.__setitem__("unknown", True),
        lambda journal: journal["prerequisite_attestation"].__setitem__("receipts", {}),
    ],
)
def test_seal_journal_shape_rejects_unknown_or_malformed_attestation(
    mutate,
) -> None:
    inventory: dict[str, JSONValue] = {
        "files": [],
        "sha256": hashlib.sha256(canonical_line({"files": []})).hexdigest(),
    }
    attestation: dict[str, JSONValue] = {
        "schema_version": 1,
        "attempt_id": "a" * 64,
        "head": HEAD,
        "base_sha": "b" * 40,
        "receipts": [],
        "attempt_inventory": inventory,
        "external_exports": [],
    }
    journal: dict[str, JSONValue] = {
        "schema_version": 1,
        "transition": "seal",
        "transition_id": "c" * 64,
        "phase": "committed",
        "inputs": {
            "prerequisite_attestation_sha256": hashlib.sha256(
                canonical_line(attestation)
            ).hexdigest()
        },
        "pointer_sha256": "d" * 64,
        "operations_sha256": "e" * 64,
        "attempt_inventory": inventory,
        "external_exports": [],
        "prerequisite_attestation": attestation,
    }
    mutate(journal)

    with pytest.raises(LedgerError, match="journal|attestation"):
        writer_module._validate_journal_shape(journal)


@pytest.mark.parametrize(
    "boundary",
    [
        "abandon:ledger_parent_fsync",
        "abandon:terminal:file_fsync",
        "abandon:terminal:replace",
        "abandon:pointer:file_fsync",
        "abandon:pointer:replace",
    ],
)
def test_abandon_recovers_each_side_effect_boundary_without_duplicate_ledger_entry(
    lifecycle: LifecycleArguments, boundary: str
) -> None:
    pointer = _begin(lifecycle)
    attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    receipt = _write_failure_receipt(attempt_dir, str(pointer["attempt_id"]), "F2")
    arguments = terminal_lifecycle_arguments(lifecycle)

    def crash(name: str) -> None:
        if name == boundary:
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        abandon_final_attempt(**arguments, failure_receipt=receipt, hook=crash)
    result = abandon_final_attempt(**arguments, failure_receipt=receipt)
    events = [
        entry
        for entry in load_verified_operations(Path(lifecycle["operations"]))
        if entry["action_class"] == "final_attempt_abandoned"
        and isinstance(entry["arguments"], dict)
        and entry["arguments"].get("attempt_id") == pointer["attempt_id"]
    ]

    assert result["status"] == "abandoned"
    assert len(events) == 1


def test_multiple_or_impossible_nonterminal_journals_fail_closed(
    lifecycle: LifecycleArguments,
) -> None:
    journal_root = Path(lifecycle["journal_root"])
    journal_root.mkdir(parents=True)
    _write_json(
        journal_root / ("begin-" + "a" * 64 + ".json"),
        {
            "schema_version": 1,
            "transition": "begin",
            "transition_id": "a" * 64,
            "phase": "nonsense",
        },
    )

    with pytest.raises(LifecycleError, match="impossible"):
        _begin(lifecycle)


CRASH_BOUNDARIES = {
    "begin": (
        "journal:prepared:file_fsync",
        "journal:prepared:parent_fsync",
        "begin:ledger_parent_fsync",
        "journal:ledger_appended:file_fsync",
        "journal:ledger_appended:parent_fsync",
        "begin:attempt_directory:directory_fsync",
        "begin:attempt_directory:parent_fsync",
        "journal:directory_created:file_fsync",
        "journal:directory_created:parent_fsync",
        "begin:pointer:file_fsync",
        "begin:pointer:parent_fsync",
        "journal:pointer_open:file_fsync",
        "journal:pointer_open:parent_fsync",
        "journal:committed:file_fsync",
        "journal:committed:parent_fsync",
    ),
    "abandon": (
        "journal:prepared:file_fsync",
        "journal:prepared:parent_fsync",
        "abandon:ledger_parent_fsync",
        "journal:ledger_appended:file_fsync",
        "journal:ledger_appended:parent_fsync",
        "abandon:terminal:file_fsync",
        "abandon:terminal:parent_fsync",
        "journal:terminal_written:file_fsync",
        "journal:terminal_written:parent_fsync",
        "abandon:pointer:file_fsync",
        "abandon:pointer:parent_fsync",
        "journal:pointer_abandoned:file_fsync",
        "journal:pointer_abandoned:parent_fsync",
        "journal:committed:file_fsync",
        "journal:committed:parent_fsync",
    ),
    "seal": (
        "journal:prepared:file_fsync",
        "journal:prepared:parent_fsync",
        "seal:file:file_fsync",
        "seal:file:parent_fsync",
        "journal:seal_written:file_fsync",
        "journal:seal_written:parent_fsync",
        "seal:pointer:file_fsync",
        "seal:pointer:parent_fsync",
        "journal:pointer_sealed:file_fsync",
        "journal:pointer_sealed:parent_fsync",
        "journal:committed:file_fsync",
        "journal:committed:parent_fsync",
    ),
    "reopen": (
        "journal:prepared:file_fsync",
        "journal:prepared:parent_fsync",
        "reopen:ledger_parent_fsync",
        "journal:ledger_appended:file_fsync",
        "journal:ledger_appended:parent_fsync",
        "reopen:pointer:file_fsync",
        "reopen:pointer:parent_fsync",
        "journal:pointer_reopened:file_fsync",
        "journal:pointer_reopened:parent_fsync",
        "journal:committed:file_fsync",
        "journal:committed:parent_fsync",
    ),
}


@pytest.mark.parametrize(
    ("transition", "boundary"),
    [
        (transition, boundary)
        for transition, boundaries in CRASH_BOUNDARIES.items()
        for boundary in boundaries
    ],
)
def test_every_lifecycle_fsync_boundary_is_exactly_recoverable(
    lifecycle: LifecycleArguments, transition: str, boundary: str
) -> None:
    pointer: dict[str, JSONValue] | None = None
    failure_receipt: Path | None = None
    output: Path | None = None
    if transition != "begin":
        pointer = _begin(lifecycle)
        attempt_dir = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
        if transition == "abandon":
            failure_receipt = _write_failure_receipt(attempt_dir, str(pointer["attempt_id"]), "F2")
        else:
            output = attempt_dir / "operations-seal.json"
            if transition == "reopen":
                _seal(lifecycle, output)
                failure_receipt = _write_review_receipt(
                    attempt_dir, str(pointer["attempt_id"]), "F1"
                )

    def crash(name: str) -> None:
        if name == boundary:
            raise RuntimeError("injected")

    arguments = terminal_lifecycle_arguments(lifecycle)

    def operation(hook: Callable[[str], None] | None) -> dict[str, JSONValue]:
        if transition == "begin":
            return begin_final_attempt(**lifecycle, hook=hook)
        if transition == "abandon":
            assert failure_receipt is not None
            return abandon_final_attempt(**arguments, failure_receipt=failure_receipt, hook=hook)
        if transition == "seal":
            assert output is not None
            return _seal(lifecycle, output, hook=hook)
        assert transition == "reopen"
        assert failure_receipt is not None
        return reopen_seal(**arguments, failure_receipt=failure_receipt, hook=hook)

    with pytest.raises(RuntimeError, match="injected"):
        operation(crash)

    if boundary == "journal:committed:parent_fsync":
        journals = list(Path(lifecycle["journal_root"]).glob(f"{transition}-*.json"))
        assert any(json.loads(path.read_text())["phase"] == "committed" for path in journals)
    else:
        recovered = operation(None)
        expected_status = {
            "begin": "open",
            "abandon": "abandoned",
            "seal": "sealed",
            "reopen": "reopened",
        }[transition]
        assert recovered["status"] == expected_status

    action = {
        "begin": "final_attempt_started",
        "abandon": "final_attempt_abandoned",
        "seal": None,
        "reopen": "seal_reopened",
    }[transition]
    if action is not None:
        attempt_id = (
            str(pointer["attempt_id"])
            if pointer is not None
            else next(Path(lifecycle["attempt_root"]).iterdir()).name
        )
        events = [
            entry
            for entry in load_verified_operations(Path(lifecycle["operations"]))
            if entry["action_class"] == action
            and isinstance(entry["arguments"], dict)
            and entry["arguments"].get("attempt_id") == attempt_id
        ]
        assert len(events) == 1


@pytest.mark.parametrize("mode", [0o660, 0o666])
def test_lifecycle_rejects_writable_pointer_and_ledger(
    lifecycle: LifecycleArguments, mode: int
) -> None:
    _begin(lifecycle)
    pointer = Path(lifecycle["pointer_path"])
    pointer.chmod(mode)
    with pytest.raises(LifecycleError, match="file identity is unsafe"):
        seal_attempt(**lifecycle, output=pointer.parent / "unused.json")
    pointer.chmod(0o600)

    operations = Path(lifecycle["operations"])
    operations.chmod(mode)
    with pytest.raises(LedgerError, match="ledger file identity is unsafe"):
        append_operation(
            operations,
            task_id="mode-test",
            action_class="verification",
            arguments={},
            status="passed",
        )
    operations.chmod(0o600)


@pytest.mark.parametrize("root_name", ["journal_root", "attempt_root"])
def test_lifecycle_rejects_symlinked_mutation_roots_before_redirected_write(
    lifecycle: LifecycleArguments, tmp_path: Path, root_name: str
) -> None:
    root = Path(lifecycle[root_name])
    outside = tmp_path / f"outside-{root_name}"
    outside.mkdir()
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(LifecycleError, match="cannot be trusted"):
        _begin(lifecycle)

    assert list(outside.iterdir()) == []


def test_lifecycle_detects_journal_parent_inode_swap_after_replace(
    lifecycle: LifecycleArguments, tmp_path: Path
) -> None:
    journal_root = Path(lifecycle["journal_root"])
    outside = tmp_path / "outside-inode-swap"
    outside.mkdir()

    def swap(boundary: str) -> None:
        if boundary == "journal:prepared:replace":
            saved = journal_root.with_name("saved-journals")
            journal_root.rename(saved)
            journal_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(LifecycleError, match="parent path changed"):
        _begin(lifecycle, hook=swap)

    assert list(outside.iterdir()) == []


def test_atomic_json_detects_post_parent_fsync_name_swap_and_preserves_foreign_inode(
    lifecycle: LifecycleArguments, tmp_path: Path
) -> None:
    replacement = tmp_path / "foreign-journal.json"
    foreign = canonical_line({"foreign": True})
    replacement.write_bytes(foreign)
    replacement.chmod(0o600)

    def swap(boundary: str) -> None:
        if boundary == "journal:prepared:parent_fsync":
            journal = next(Path(lifecycle["journal_root"]).glob("begin-*.json"))
            replacement.replace(journal)

    with pytest.raises(LifecycleError, match="write identity changed"):
        _begin(lifecycle, hook=swap)

    journals = list(Path(lifecycle["journal_root"]).glob("begin-*.json"))
    assert len(journals) == 1
    assert journals[0].read_bytes() == foreign


@pytest.mark.parametrize(
    "attack",
    ["nonterminal", "sealed", "no-active", "missing-ledger", "forged-open"],
)
def test_read_only_final_attempt_authority_rejects_unproven_active_relation(
    lifecycle: LifecycleArguments, attack: str
) -> None:
    pointer = _begin(lifecycle)
    evidence_root = Path(lifecycle["operations"]).parent
    operations = Path(lifecycle["operations"])
    pointer_path = Path(lifecycle["pointer_path"])
    attempt_path = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    if attack == "nonterminal":
        journal_path = next(Path(lifecycle["journal_root"]).glob("begin-*.json"))
        journal = json.loads(journal_path.read_bytes())
        journal["phase"] = "prepared"
        _write_json(journal_path, journal)
    elif attack == "sealed":
        forged = dict(pointer)
        forged.update({"status": "sealed", "seal_id": "a" * 64, "seal_sha256": "b" * 64})
        _write_json(pointer_path, forged)
    elif attack == "no-active":
        copy_isolated_operations(operations)
    elif attack == "missing-ledger":
        operations.rename(operations.with_suffix(".missing"))
    elif attack == "forged-open":
        forged = dict(pointer)
        forged["head"] = "e" * 40
        _write_json(pointer_path, forged)
    descriptor = os.open(evidence_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with pytest.raises(FinalAttemptAuthorityError):
            verify_open_final_attempt_authority(
                evidence_root=evidence_root,
                evidence_root_fd=descriptor,
                expected_attempt_id=str(pointer["attempt_id"]),
                expected_attempt_path=attempt_path,
                expected_head=HEAD,
            )
    finally:
        os.close(descriptor)


def test_read_only_final_attempt_authority_accepts_exact_open_active_relation(
    lifecycle: LifecycleArguments,
) -> None:
    pointer = _begin(lifecycle)
    evidence_root = Path(lifecycle["operations"]).parent
    attempt_path = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    descriptor = os.open(evidence_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        verified = verify_open_final_attempt_authority(
            evidence_root=evidence_root,
            evidence_root_fd=descriptor,
            expected_attempt_id=str(pointer["attempt_id"]),
            expected_attempt_path=attempt_path,
            expected_head=HEAD,
        )
    finally:
        os.close(descriptor)

    assert verified == pointer


@pytest.mark.parametrize("post_seal_action", [None, "verification"])
def test_read_only_final_attempt_authority_rejects_external_ledger_seal(
    lifecycle: LifecycleArguments, post_seal_action: str | None
) -> None:
    pointer = _begin(lifecycle)
    operations = Path(lifecycle["operations"])
    _append_forged_lifecycle_entry(
        operations,
        action_class="seal",
        arguments={},
    )
    if post_seal_action is not None:
        _append_forged_lifecycle_entry(
            operations,
            action_class=post_seal_action,
            arguments={},
        )
    evidence_root = operations.parent
    attempt_path = Path(lifecycle["repo_root"]) / str(pointer["attempt_dir"])
    descriptor = os.open(evidence_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with pytest.raises(FinalAttemptAuthorityError, match="externally sealed"):
            verify_open_final_attempt_authority(
                evidence_root=evidence_root,
                evidence_root_fd=descriptor,
                expected_attempt_id=str(pointer["attempt_id"]),
                expected_attempt_path=attempt_path,
                expected_head=HEAD,
            )
    finally:
        os.close(descriptor)


def test_lifecycle_rejects_same_uid_evidence_root_replacement_before_mutation(
    lifecycle: LifecycleArguments, monkeypatch: pytest.MonkeyPatch
) -> None:
    operations = Path(lifecycle["operations"])
    evidence_root = operations.parent
    displaced = evidence_root.with_name("displaced-evidence")
    original_revalidate = io_module._revalidate_evidence_root
    replaced = False

    def replace_then_validate(lock: EvidenceWriterLock) -> None:
        nonlocal replaced
        if not replaced:
            replaced = True
            evidence_root.rename(displaced)
            evidence_root.mkdir(mode=0o700)
        original_revalidate(lock)

    monkeypatch.setattr(io_module, "_revalidate_evidence_root", replace_then_validate)

    with pytest.raises(LifecycleError, match="cannot be trusted"):
        _begin(lifecycle)

    assert list(evidence_root.iterdir()) == []
    assert not (evidence_root / "lifecycle-journals").exists()


def test_lifecycle_rejects_multiple_and_mismatched_pending_journals(
    lifecycle: LifecycleArguments,
) -> None:
    _begin(lifecycle)
    journal_root = Path(lifecycle["journal_root"])
    committed = next(journal_root.glob("begin-*.json"))
    journal = json.loads(committed.read_text())
    journal["phase"] = "prepared"
    _write_json(journal_root / "copy-one.json", journal)
    _write_json(journal_root / "copy-two.json", journal)
    with pytest.raises(LifecycleError, match="multiple nonterminal"):
        _begin(lifecycle)

    (journal_root / "copy-two.json").unlink()
    journal["inputs"]["plan_sha256"] = "0" * 64
    _write_json(journal_root / "copy-one.json", journal)
    with pytest.raises(LifecycleError, match="another plan identity"):
        _begin(lifecycle)
