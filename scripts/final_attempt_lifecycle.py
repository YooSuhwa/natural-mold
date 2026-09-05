"""Crash-recoverable final-attempt lifecycle transitions."""

from __future__ import annotations

import stat
from pathlib import Path

from final_attempt_evidence import (
    _external_exports,
    _failure_receipt,
    _inventory,
    _validate_flat_attempt_directory,
    _validate_preseal_files,
    _validate_transition_inventory,
)
from final_attempt_io import (
    SHA40,
    SHA64,
    CrashHook,
    EvidenceWriterLock,
    FinalGateOutputBinding,
    LifecycleError,
    _atomic_json,
    _create_directory,
    _digest_bytes,
    _digest_json,
    _ensure_directory,
    _entry_metadata,
    _events,
    _hash_optional,
    _read_json,
    _read_optional,
    _read_regular,
    _tip,
    bind_final_gate_output,
    load_bound_operations,
    write_bound_final_gate_output,
)
from final_attempt_review_contract import (
    prerequisite_attestation,
    validate_f2,
    validate_f3,
)
from final_attempt_state import (
    _advance,
    _assert_prior_frozen,
    _attempt_pointer,
    _journal_candidates,
    _safe_relative,
    _validate_contract,
    _validate_journal_plan,
    _validate_layout,
    validate_current_pointer,
)
from operation_ledger_format import JSONValue
from operation_ledger_writer import (
    _append_final_attempt_lifecycle_operation,
    evidence_writer_lock,
)
from plan_history_contract import PlanContract, PlanHistoryError

POSTSEAL_OUTPUTS = frozenset({"f1-history.json", "f1-review.md", "f4-scope.json", "f4-review.md"})


def _attestation_hashes(attestation: dict[str, JSONValue]) -> tuple[str, str, str]:
    inventory = attestation.get("attempt_inventory")
    exports = attestation.get("external_exports")
    if not isinstance(inventory, dict) or not isinstance(exports, list):
        raise LifecycleError("prerequisite attestation schema is invalid")
    return (
        _digest_json(attestation),
        _digest_json(inventory),
        _digest_json({"external_exports": exports}),
    )


def begin_final_attempt(
    *,
    repo_root: Path,
    contract: PlanContract,
    plan_sha: str,
    review_round: str,
    operations: Path,
    head: str,
    attempt_root: Path,
    pointer_path: Path,
    journal_root: Path,
    hook: CrashHook | None = None,
) -> dict[str, JSONValue]:
    _validate_contract(contract, plan_sha, review_round)
    _validate_layout(operations, pointer_path, journal_root, attempt_root)
    with evidence_writer_lock(operations) as evidence_lock:
        _ensure_directory(journal_root, evidence_lock)
        _ensure_directory(attempt_root, evidence_lock)
        pending = _journal_candidates(journal_root)
        if pending:
            journal_path, journal = pending[0]
            if journal.get("transition") != "begin":
                raise LifecycleError("another lifecycle transition is nonterminal")
            attempt_id = str(journal.get("transition_id"))
        else:
            prior = _attempt_pointer(pointer_path)
            if prior is not None:
                _assert_prior_frozen(repo_root, prior)
            pre_tip = _tip(operations)
            identity: dict[str, JSONValue] = {
                "head": head,
                "plan_sha256": plan_sha,
                "review_round": review_round,
                "operations_terminal_hash": pre_tip,
            }
            attempt_id = _digest_json(identity)
            attempt_dir = attempt_root / attempt_id
            if _entry_metadata(attempt_dir) is not None:
                raise LifecycleError("final attempt ID collides with an existing path")
            journal_path = journal_root / f"begin-{attempt_id}.json"
            if _entry_metadata(journal_path) is not None:
                raise LifecycleError("begin transition was already committed")
            journal = {
                "schema_version": 1,
                "transition": "begin",
                "transition_id": attempt_id,
                "phase": "prepared",
                "inputs": identity,
                "prior_pointer_sha256": _hash_optional(pointer_path),
            }
            _atomic_json(journal_path, journal, hook, "journal:prepared", evidence_lock)
        _validate_journal_plan(journal, plan_sha, review_round)
        return _resume_begin(
            repo_root,
            journal_path,
            journal,
            operations,
            head,
            attempt_root,
            pointer_path,
            hook,
            evidence_lock,
        )


def _resume_begin(
    repo_root: Path,
    journal_path: Path,
    journal: dict[str, JSONValue],
    operations: Path,
    head: str,
    attempt_root: Path,
    pointer_path: Path,
    hook: CrashHook | None,
    evidence_lock: EvidenceWriterLock,
) -> dict[str, JSONValue]:
    attempt_id = str(journal["transition_id"])
    inputs = journal.get("inputs")
    expected_input_keys = {
        "head",
        "plan_sha256",
        "review_round",
        "operations_terminal_hash",
    }
    if (
        not isinstance(inputs, dict)
        or set(inputs) != expected_input_keys
        or inputs.get("head") != head
        or _digest_json(inputs) != attempt_id
        or journal_path.name != f"begin-{attempt_id}.json"
    ):
        raise LifecycleError("begin journal immutable inputs changed")
    attempt_dir = attempt_root / attempt_id
    expected_pointer: dict[str, JSONValue] = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "attempt_dir": _safe_relative(repo_root, attempt_dir),
        "status": "open",
        "head": head,
    }
    phase = str(journal["phase"])
    event_arguments: dict[str, JSONValue] = {"attempt_id": attempt_id, "head": head}
    events = _events(operations, "final_attempt_started", event_arguments)
    if (
        phase in {"directory_created", "pointer_open"}
        and _inventory(attempt_dir).get("files") != []
    ):
        raise LifecycleError("open attempt inventory changed during begin recovery")
    if len(events) > 1:
        raise LifecycleError("duplicate final_attempt_started ledger entries")
    if events:
        if events[0].get("previous_entry_hash") != inputs["operations_terminal_hash"]:
            raise LifecycleError("begin ledger entry is not bound to the prepared tip")
    elif _tip(operations) != inputs["operations_terminal_hash"]:
        raise LifecycleError("operations ledger changed before begin append")
    if phase == "prepared":
        if _hash_optional(pointer_path) != journal.get("prior_pointer_sha256"):
            raise LifecycleError("prior pointer changed during begin recovery")
        if not events:

            def revalidate_begin() -> None:
                if (
                    _hash_optional(pointer_path) != journal.get("prior_pointer_sha256")
                    or _tip(operations) != inputs["operations_terminal_hash"]
                    or _events(operations, "final_attempt_started", event_arguments)
                ):
                    raise LifecycleError("begin transition changed before ledger append")

            _append_final_attempt_lifecycle_operation(
                operations,
                evidence_lock=evidence_lock,
                expected_previous_hash=str(inputs["operations_terminal_hash"]),
                revalidate=revalidate_begin,
            )
            if hook is not None:
                hook("begin:ledger_parent_fsync")
        _advance(journal_path, journal, "ledger_appended", hook, evidence_lock)
        phase = "ledger_appended"
    if phase == "ledger_appended":
        attempt_metadata = _entry_metadata(attempt_dir)
        if attempt_metadata is None:
            _create_directory(attempt_dir, hook, "begin:attempt_directory", evidence_lock)
        elif (
            not stat.S_ISDIR(attempt_metadata.st_mode) or _inventory(attempt_dir).get("files") != []
        ):
            raise LifecycleError("attempt directory collision is not empty and exact")
        _advance(journal_path, journal, "directory_created", hook, evidence_lock)
        phase = "directory_created"
    if phase == "directory_created":
        pointer = _read_optional(pointer_path)
        if pointer is None or _hash_optional(pointer_path) == journal.get("prior_pointer_sha256"):
            _atomic_json(pointer_path, expected_pointer, hook, "begin:pointer", evidence_lock)
        elif pointer != expected_pointer:
            raise LifecycleError("begin pointer is not the exact recoverable value")
        _advance(journal_path, journal, "pointer_open", hook, evidence_lock)
        phase = "pointer_open"
    if phase == "pointer_open":
        if _read_json(pointer_path) != expected_pointer:
            raise LifecycleError("open pointer changed before begin commit")
        _advance(journal_path, journal, "committed", hook, evidence_lock)
    return expected_pointer


def abandon_final_attempt(
    *,
    repo_root: Path,
    contract: PlanContract,
    plan_sha: str,
    review_round: str,
    operations: Path,
    pointer_path: Path,
    attempt_root: Path,
    failure_receipt: Path,
    journal_root: Path,
    hook: CrashHook | None = None,
) -> dict[str, JSONValue]:
    return _terminal_transition(
        "abandon",
        repo_root,
        contract,
        plan_sha,
        review_round,
        operations,
        pointer_path,
        failure_receipt,
        journal_root,
        attempt_root,
        hook,
    )


def reopen_seal(
    *,
    repo_root: Path,
    contract: PlanContract,
    plan_sha: str,
    review_round: str,
    operations: Path,
    pointer_path: Path,
    attempt_root: Path,
    failure_receipt: Path,
    journal_root: Path,
    hook: CrashHook | None = None,
) -> dict[str, JSONValue]:
    return _terminal_transition(
        "reopen",
        repo_root,
        contract,
        plan_sha,
        review_round,
        operations,
        pointer_path,
        failure_receipt,
        journal_root,
        attempt_root,
        hook,
    )


def _terminal_transition(
    kind: str,
    repo_root: Path,
    contract: PlanContract,
    plan_sha: str,
    review_round: str,
    operations: Path,
    pointer_path: Path,
    failure_receipt: Path,
    journal_root: Path,
    attempt_root: Path,
    hook: CrashHook | None,
) -> dict[str, JSONValue]:
    _validate_contract(contract, plan_sha, review_round)
    _validate_layout(operations, pointer_path, journal_root, attempt_root)
    with evidence_writer_lock(operations) as evidence_lock:
        _ensure_directory(journal_root, evidence_lock)
        _ensure_directory(attempt_root, evidence_lock)
        pending = _journal_candidates(journal_root)
        if pending:
            journal_path, journal = pending[0]
            if journal.get("transition") != kind:
                raise LifecycleError("another lifecycle transition is nonterminal")
        else:
            pointer = _attempt_pointer(pointer_path)
            required_status = "open" if kind == "abandon" else "sealed"
            if pointer is None or pointer.get("status") != required_status:
                raise LifecycleError(f"{kind} requires a {required_status} attempt")
            attempt_id = str(pointer["attempt_id"])
            attempt_dir = repo_root / str(pointer["attempt_dir"])
            if attempt_dir.parent.absolute() != attempt_root.absolute():
                raise LifecycleError("pointer attempt directory is outside the fixed attempt root")
            head = str(pointer["head"])
            failure_sha = _failure_receipt(failure_receipt, attempt_dir, attempt_id, kind, head)
            failure_path = _safe_relative(repo_root, failure_receipt)
            pre_tip = _tip(operations)
            seal_sha = None
            if kind == "reopen":
                seal_sha = _digest_bytes(_read_regular(attempt_dir / "operations-seal.json"))
            identity: dict[str, JSONValue] = {
                "attempt_id": attempt_id,
                "plan_sha256": plan_sha,
                "review_round": review_round,
                "failure_receipt_sha256": failure_sha,
                "failure_receipt_path": failure_path,
                "operations_terminal_hash": pre_tip,
                "operations_seal_sha256": seal_sha,
            }
            transition_id = _digest_json(identity)
            journal_path = journal_root / f"{kind}-{transition_id}.json"
            if _entry_metadata(journal_path) is not None:
                raise LifecycleError(f"{kind} transition was already committed")
            journal = {
                "schema_version": 1,
                "transition": kind,
                "transition_id": transition_id,
                "phase": "prepared",
                "inputs": identity,
                "pointer_sha256": _digest_bytes(_read_regular(pointer_path)),
                "attempt_inventory": _inventory(attempt_dir),
                "external_exports": _external_exports(repo_root, attempt_dir, attempt_id),
            }
            _atomic_json(journal_path, journal, hook, "journal:prepared", evidence_lock)
        _validate_journal_plan(journal, plan_sha, review_round)
        return _resume_terminal(
            kind,
            repo_root,
            journal_path,
            journal,
            operations,
            pointer_path,
            failure_receipt,
            attempt_root,
            hook,
            evidence_lock,
        )


def _resume_terminal(
    kind: str,
    repo_root: Path,
    journal_path: Path,
    journal: dict[str, JSONValue],
    operations: Path,
    pointer_path: Path,
    failure_receipt: Path,
    attempt_root: Path,
    hook: CrashHook | None,
    evidence_lock: EvidenceWriterLock,
) -> dict[str, JSONValue]:
    pointer = _attempt_pointer(pointer_path)
    inputs = journal.get("inputs")
    expected_input_keys = {
        "attempt_id",
        "plan_sha256",
        "review_round",
        "failure_receipt_sha256",
        "failure_receipt_path",
        "operations_terminal_hash",
        "operations_seal_sha256",
    }
    if pointer is None or not isinstance(inputs, dict) or set(inputs) != expected_input_keys:
        raise LifecycleError(f"{kind} journal inputs are invalid")
    attempt_id = str(inputs["attempt_id"])
    if pointer.get("attempt_id") != attempt_id:
        raise LifecycleError(f"{kind} journal belongs to another attempt")
    attempt_dir = repo_root / str(pointer["attempt_dir"])
    if attempt_dir.parent.absolute() != attempt_root.absolute():
        raise LifecycleError("pointer attempt directory is outside the fixed attempt root")
    head = str(pointer["head"])
    failure_sha = _failure_receipt(failure_receipt, attempt_dir, attempt_id, kind, head)
    if failure_sha != inputs.get("failure_receipt_sha256"):
        raise LifecycleError("failure receipt changed during lifecycle recovery")
    if _safe_relative(repo_root, failure_receipt) != inputs.get("failure_receipt_path"):
        raise LifecycleError("failure receipt path changed during lifecycle recovery")
    if _external_exports(repo_root, attempt_dir, attempt_id) != journal.get("external_exports"):
        raise LifecycleError("external export inventory changed during lifecycle recovery")
    phase = str(journal["phase"])
    if kind == "abandon":
        _validate_transition_inventory(
            attempt_dir,
            journal.get("attempt_inventory"),
            excluded=frozenset({"attempt-abandoned.json"}),
        )
    else:
        _validate_transition_inventory(attempt_dir, journal.get("attempt_inventory"))
    expected_initial = "open" if kind == "abandon" else "sealed"
    final_status = "abandoned" if kind == "abandon" else "reopened"
    action = "final_attempt_abandoned" if kind == "abandon" else "seal_reopened"
    key = "abandon_id" if kind == "abandon" else "reopen_id"
    transition_id = str(journal["transition_id"])
    event_arguments: dict[str, JSONValue] = {
        key: transition_id,
        "attempt_id": attempt_id,
        "failure_sha256": failure_sha,
        "failure_path": str(inputs["failure_receipt_path"]),
    }
    expected_identity: dict[str, JSONValue] = {
        "attempt_id": attempt_id,
        "plan_sha256": str(inputs["plan_sha256"]),
        "review_round": str(inputs["review_round"]),
        "failure_receipt_sha256": failure_sha,
        "failure_receipt_path": str(inputs["failure_receipt_path"]),
        "operations_terminal_hash": str(inputs["operations_terminal_hash"]),
        "operations_seal_sha256": inputs.get("operations_seal_sha256"),
    }
    if _digest_json(expected_identity) != transition_id:
        raise LifecycleError("terminal transition identity is not reproducible")
    if journal_path.name != f"{kind}-{transition_id}.json":
        raise LifecycleError("terminal journal filename disagrees with its identity")
    events = _events(operations, action, event_arguments)
    if len(events) > 1:
        raise LifecycleError(f"duplicate {action} ledger entries")
    if events:
        if events[0].get("previous_entry_hash") != inputs["operations_terminal_hash"]:
            raise LifecycleError(f"{kind} ledger entry is not bound to the prepared tip")
    elif _tip(operations) != inputs["operations_terminal_hash"]:
        raise LifecycleError(f"operations ledger changed before {kind} append")
    if phase == "prepared":
        if pointer.get("status") != expected_initial or _digest_bytes(
            _read_regular(pointer_path)
        ) != journal.get("pointer_sha256"):
            raise LifecycleError(f"{kind} pointer changed during recovery")
        if not events:

            def revalidate_terminal() -> None:
                current_pointer = _attempt_pointer(pointer_path)
                if (
                    current_pointer is None
                    or current_pointer.get("status") != expected_initial
                    or current_pointer.get("attempt_id") != attempt_id
                    or _digest_bytes(_read_regular(pointer_path)) != journal.get("pointer_sha256")
                    or _failure_receipt(failure_receipt, attempt_dir, attempt_id, kind, head)
                    != failure_sha
                    or _safe_relative(repo_root, failure_receipt)
                    != inputs.get("failure_receipt_path")
                    or _external_exports(repo_root, attempt_dir, attempt_id)
                    != journal.get("external_exports")
                    or _digest_json(expected_identity) != transition_id
                    or _tip(operations) != inputs["operations_terminal_hash"]
                    or _events(operations, action, event_arguments)
                ):
                    raise LifecycleError(f"{kind} transition changed before ledger append")
                if kind == "abandon":
                    _validate_transition_inventory(
                        attempt_dir,
                        journal.get("attempt_inventory"),
                        excluded=frozenset({"attempt-abandoned.json"}),
                    )
                else:
                    _validate_transition_inventory(attempt_dir, journal.get("attempt_inventory"))

            _append_final_attempt_lifecycle_operation(
                operations,
                evidence_lock=evidence_lock,
                expected_previous_hash=str(inputs["operations_terminal_hash"]),
                revalidate=revalidate_terminal,
            )
            if hook is not None:
                hook(f"{kind}:ledger_parent_fsync")
        _advance(journal_path, journal, "ledger_appended", hook, evidence_lock)
        phase = "ledger_appended"
    marker_path = attempt_dir / "attempt-abandoned.json"
    marker: dict[str, JSONValue] | None = None
    if kind == "abandon":
        marker = {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "abandon_id": transition_id,
            "failure_receipt_sha256": failure_sha,
            "attempt_inventory": journal["attempt_inventory"],
        }
        if phase in {"terminal_written", "pointer_abandoned"} and _read_json(marker_path) != marker:
            raise LifecycleError("abandon terminal marker changed during recovery")
    if kind == "abandon" and phase == "ledger_appended":
        if marker is None:
            raise LifecycleError("abandon terminal marker is unavailable")
        existing = _read_optional(marker_path)
        if existing is None:
            _atomic_json(marker_path, marker, hook, "abandon:terminal", evidence_lock)
        elif existing != marker:
            raise LifecycleError("abandon terminal marker is not exact")
        _advance(journal_path, journal, "terminal_written", hook, evidence_lock)
        phase = "terminal_written"
    pointer_phase = "terminal_written" if kind == "abandon" else "ledger_appended"
    final_phase = "pointer_abandoned" if kind == "abandon" else "pointer_reopened"
    expected_pointer = dict(pointer)
    expected_pointer["status"] = final_status
    expected_pointer["transition_id"] = transition_id
    expected_pointer["frozen_inventory"] = _inventory(attempt_dir)
    if phase == pointer_phase:
        current = _read_json(pointer_path)
        if current.get("status") == expected_initial:
            _atomic_json(pointer_path, expected_pointer, hook, f"{kind}:pointer", evidence_lock)
        elif current != expected_pointer:
            raise LifecycleError(f"{kind} pointer state is impossible")
        _advance(journal_path, journal, final_phase, hook, evidence_lock)
        phase = final_phase
    if phase == final_phase:
        if _read_json(pointer_path) != expected_pointer:
            raise LifecycleError(f"{kind} pointer changed before commit")
        _advance(journal_path, journal, "committed", hook, evidence_lock)
    return expected_pointer


def seal_attempt(
    *,
    repo_root: Path,
    contract: PlanContract,
    plan_sha: str,
    review_round: str,
    operations: Path,
    head: str,
    pointer_path: Path,
    output: Path,
    journal_root: Path,
    attempt_root: Path,
    hook: CrashHook | None = None,
) -> dict[str, JSONValue]:
    _validate_contract(contract, plan_sha, review_round)
    _validate_layout(operations, pointer_path, journal_root, attempt_root)
    with evidence_writer_lock(operations) as evidence_lock:
        _ensure_directory(journal_root, evidence_lock)
        _ensure_directory(attempt_root, evidence_lock)
        pending = _journal_candidates(journal_root)
        if pending:
            journal_path, journal = pending[0]
            if journal.get("transition") != "seal":
                raise LifecycleError("another lifecycle transition is nonterminal")
        else:
            pointer = _attempt_pointer(pointer_path)
            if pointer is None or pointer.get("status") != "open" or pointer.get("head") != head:
                raise LifecycleError("seal requires the exact open attempt and HEAD")
            attempt_dir = repo_root / str(pointer["attempt_dir"])
            if attempt_dir.parent.absolute() != attempt_root.absolute():
                raise LifecycleError("pointer attempt directory is outside the fixed attempt root")
            if output.absolute() != (attempt_dir / "operations-seal.json").absolute():
                raise LifecycleError("operations seal must be attempt-local")
            _validate_preseal_files(attempt_dir)
            validate_f2(
                repo_root,
                attempt_dir,
                str(pointer["attempt_id"]),
                head,
                contract.base_sha,
            )
            validate_f3(repo_root, attempt_dir, str(pointer["attempt_id"]), head)
            operations_hash = _digest_bytes(_read_regular(operations))
            attempt_inventory = _inventory(attempt_dir)
            external_exports = _external_exports(repo_root, attempt_dir, str(pointer["attempt_id"]))
            attestation = prerequisite_attestation(
                repo_root,
                attempt_dir,
                str(pointer["attempt_id"]),
                head,
                contract.base_sha,
                attempt_inventory,
                external_exports,
            )
            attestation_sha, inventory_sha, exports_sha = _attestation_hashes(attestation)
            identity: dict[str, JSONValue] = {
                "attempt_id": str(pointer["attempt_id"]),
                "head": head,
                "plan_sha256": plan_sha,
                "review_round": review_round,
                "operations_terminal_hash": _tip(operations),
                "prerequisite_attestation_sha256": attestation_sha,
                "attempt_inventory_sha256": inventory_sha,
                "external_exports_sha256": exports_sha,
            }
            seal_id = _digest_json(identity)
            journal_path = journal_root / f"seal-{seal_id}.json"
            if _entry_metadata(journal_path) is not None:
                raise LifecycleError("seal transition was already committed")
            journal = {
                "schema_version": 1,
                "transition": "seal",
                "transition_id": seal_id,
                "phase": "prepared",
                "inputs": identity,
                "pointer_sha256": _digest_bytes(_read_regular(pointer_path)),
                "operations_sha256": operations_hash,
                "attempt_inventory": attempt_inventory,
                "external_exports": external_exports,
                "prerequisite_attestation": attestation,
            }
            _atomic_json(journal_path, journal, hook, "journal:prepared", evidence_lock)
        _validate_journal_plan(journal, plan_sha, review_round)
        return _resume_seal(
            repo_root,
            journal_path,
            journal,
            operations,
            head,
            pointer_path,
            output,
            hook,
            evidence_lock,
            contract.base_sha,
        )


def _resume_seal(
    repo_root: Path,
    journal_path: Path,
    journal: dict[str, JSONValue],
    operations: Path,
    head: str,
    pointer_path: Path,
    output: Path,
    hook: CrashHook | None,
    evidence_lock: EvidenceWriterLock,
    expected_base_sha: str,
) -> dict[str, JSONValue]:
    pointer = _attempt_pointer(pointer_path)
    inputs = journal.get("inputs")
    expected_input_keys = {
        "attempt_id",
        "head",
        "plan_sha256",
        "review_round",
        "operations_terminal_hash",
        "prerequisite_attestation_sha256",
        "attempt_inventory_sha256",
        "external_exports_sha256",
    }
    if (
        pointer is None
        or not isinstance(inputs, dict)
        or set(inputs) != expected_input_keys
        or inputs.get("head") != head
        or _digest_json(inputs) != journal.get("transition_id")
        or journal_path.name != f"seal-{journal.get('transition_id')}.json"
    ):
        raise LifecycleError("seal journal inputs changed")
    attempt_dir = repo_root / str(pointer["attempt_dir"])
    if inputs.get("attempt_id") != pointer.get("attempt_id"):
        raise LifecycleError("seal journal belongs to another attempt")
    _validate_flat_attempt_directory(attempt_dir)
    attempt_inventory = _inventory(attempt_dir, excluded=frozenset({"operations-seal.json"}))
    external_exports = _external_exports(repo_root, attempt_dir, str(pointer["attempt_id"]))
    attestation_value = journal.get("prerequisite_attestation")
    if not isinstance(attestation_value, dict):
        raise LifecycleError("seal prerequisite attestation is missing")
    base_sha = attestation_value.get("base_sha")
    if base_sha != expected_base_sha:
        raise LifecycleError("seal prerequisite base disagrees with tracked contract")
    current_attestation = prerequisite_attestation(
        repo_root,
        attempt_dir,
        str(pointer["attempt_id"]),
        head,
        expected_base_sha,
        attempt_inventory,
        external_exports,
    )
    attestation_sha, inventory_sha, exports_sha = _attestation_hashes(current_attestation)
    if external_exports != journal.get("external_exports"):
        raise LifecycleError("external export inventory changed during seal recovery")
    if (
        current_attestation != attestation_value
        or attestation_sha != inputs.get("prerequisite_attestation_sha256")
        or inventory_sha != inputs.get("attempt_inventory_sha256")
        or exports_sha != inputs.get("external_exports_sha256")
    ):
        raise LifecycleError("seal prerequisite attestation changed during recovery")

    def revalidate_prerequisites() -> None:
        _validate_flat_attempt_directory(attempt_dir)
        current_inventory = _inventory(attempt_dir, excluded=frozenset({"operations-seal.json"}))
        current_exports = _external_exports(repo_root, attempt_dir, str(pointer["attempt_id"]))
        current = prerequisite_attestation(
            repo_root,
            attempt_dir,
            str(pointer["attempt_id"]),
            head,
            expected_base_sha,
            current_inventory,
            current_exports,
        )
        current_hashes = _attestation_hashes(current)
        if current != attestation_value or current_hashes != (
            inputs.get("prerequisite_attestation_sha256"),
            inputs.get("attempt_inventory_sha256"),
            inputs.get("external_exports_sha256"),
        ):
            raise LifecycleError("seal prerequisite attestation changed during recovery")

    _validate_transition_inventory(
        attempt_dir,
        journal.get("attempt_inventory"),
        excluded=frozenset({"operations-seal.json"}),
    )
    if output.absolute() != (attempt_dir / "operations-seal.json").absolute():
        raise LifecycleError("seal output changed during recovery")
    seal: dict[str, JSONValue] = {
        "schema_version": 1,
        "attempt_id": str(pointer["attempt_id"]),
        "seal_id": str(journal["transition_id"]),
        "head": head,
        "plan_sha256": str(inputs["plan_sha256"]),
        "review_round": str(inputs["review_round"]),
        "operations_terminal_hash": str(inputs["operations_terminal_hash"]),
        "operations_sha256": str(journal["operations_sha256"]),
        "prerequisite_attestation_sha256": attestation_sha,
        "attempt_inventory_sha256": inventory_sha,
        "external_exports_sha256": exports_sha,
    }
    phase = str(journal["phase"])
    if phase == "prepared":
        if pointer.get("status") != "open" or _digest_bytes(
            _read_regular(pointer_path)
        ) != journal.get("pointer_sha256"):
            raise LifecycleError("seal pointer changed during recovery")
        existing = _read_optional(output)
        if existing is None:
            _atomic_json(output, seal, hook, "seal:file", evidence_lock)
        elif existing != seal:
            raise LifecycleError("existing operations seal is not exact")
        revalidate_prerequisites()
        _advance(journal_path, journal, "seal_written", hook, evidence_lock)
        phase = "seal_written"
    expected_pointer = dict(pointer)
    expected_pointer["status"] = "sealed"
    expected_pointer["seal_id"] = str(journal["transition_id"])
    expected_pointer["seal_sha256"] = _digest_bytes(_read_regular(output))
    if phase == "seal_written":
        revalidate_prerequisites()
        current = _read_json(pointer_path)
        if current.get("status") == "open":
            _atomic_json(pointer_path, expected_pointer, hook, "seal:pointer", evidence_lock)
        elif current != expected_pointer:
            raise LifecycleError("seal pointer state is impossible")
        _advance(journal_path, journal, "pointer_sealed", hook, evidence_lock)
        phase = "pointer_sealed"
    if phase == "pointer_sealed":
        revalidate_prerequisites()
        if _read_json(pointer_path) != expected_pointer:
            raise LifecycleError("sealed pointer changed before commit")
        if _digest_bytes(_read_regular(operations)) != seal["operations_sha256"]:
            raise LifecycleError("operations ledger changed after seal")
        _advance(journal_path, journal, "committed", hook, evidence_lock)
    return expected_pointer


def validate_seal(
    *,
    contract: PlanContract,
    operations: Path,
    seal_path: Path,
    pointer_path: Path,
    expected_head: str,
    expected_attempt_id: str,
    expected_plan_sha: str,
    expected_review_round: str,
) -> dict[str, JSONValue]:
    _validate_contract(contract, expected_plan_sha, expected_review_round)
    seal = _read_json(seal_path)
    required = {
        "schema_version",
        "attempt_id",
        "seal_id",
        "head",
        "plan_sha256",
        "review_round",
        "operations_terminal_hash",
        "operations_sha256",
        "prerequisite_attestation_sha256",
        "attempt_inventory_sha256",
        "external_exports_sha256",
    }
    if set(seal) != required or seal.get("schema_version") != 1:
        raise LifecycleError("operations seal schema is invalid")
    if (
        seal.get("head") != expected_head
        or seal.get("attempt_id") != expected_attempt_id
        or seal.get("plan_sha256") != expected_plan_sha
        or seal.get("review_round") != expected_review_round
        or SHA40.fullmatch(expected_head) is None
        or SHA64.fullmatch(expected_attempt_id) is None
        or not isinstance(seal.get("plan_sha256"), str)
        or SHA64.fullmatch(str(seal["plan_sha256"])) is None
        or not isinstance(seal.get("review_round"), str)
        or not str(seal["review_round"])
        or not isinstance(seal.get("operations_terminal_hash"), str)
        or SHA64.fullmatch(str(seal["operations_terminal_hash"])) is None
        or not isinstance(seal.get("operations_sha256"), str)
        or SHA64.fullmatch(str(seal["operations_sha256"])) is None
        or not isinstance(seal.get("prerequisite_attestation_sha256"), str)
        or SHA64.fullmatch(str(seal["prerequisite_attestation_sha256"])) is None
        or not isinstance(seal.get("attempt_inventory_sha256"), str)
        or SHA64.fullmatch(str(seal["attempt_inventory_sha256"])) is None
        or not isinstance(seal.get("external_exports_sha256"), str)
        or SHA64.fullmatch(str(seal["external_exports_sha256"])) is None
    ):
        raise LifecycleError("operations seal is bound to another HEAD or attempt")
    if seal.get("operations_sha256") != _digest_bytes(_read_regular(operations)):
        raise LifecycleError("operations ledger was mutated after seal")
    if seal.get("operations_terminal_hash") != _tip(operations):
        raise LifecycleError("operations terminal hash disagrees with the seal")
    attempt_dir = seal_path.parent
    repo_root = operations.absolute().parents[3]
    _validate_flat_attempt_directory(attempt_dir)
    inventory = _inventory(
        attempt_dir,
        excluded=frozenset({"operations-seal.json", *POSTSEAL_OUTPUTS}),
    )
    exports = _external_exports(repo_root, attempt_dir, expected_attempt_id)
    aggregate = _read_json(attempt_dir / "f2-static.json")
    if aggregate.get("base_sha") != contract.base_sha:
        raise LifecycleError("sealed F2 base disagrees with tracked contract")
    attestation = prerequisite_attestation(
        repo_root,
        attempt_dir,
        expected_attempt_id,
        expected_head,
        contract.base_sha,
        inventory,
        exports,
    )
    attestation_sha, inventory_sha, exports_sha = _attestation_hashes(attestation)
    if (
        attestation_sha != seal.get("prerequisite_attestation_sha256")
        or inventory_sha != seal.get("attempt_inventory_sha256")
        or exports_sha != seal.get("external_exports_sha256")
    ):
        raise LifecycleError("sealed prerequisite attestation changed")
    identity: dict[str, JSONValue] = {
        "attempt_id": expected_attempt_id,
        "head": expected_head,
        "plan_sha256": expected_plan_sha,
        "review_round": expected_review_round,
        "operations_terminal_hash": str(seal["operations_terminal_hash"]),
        "prerequisite_attestation_sha256": str(seal["prerequisite_attestation_sha256"]),
        "attempt_inventory_sha256": str(seal["attempt_inventory_sha256"]),
        "external_exports_sha256": str(seal["external_exports_sha256"]),
    }
    if seal.get("seal_id") != _digest_json(identity):
        raise LifecycleError("operations seal identity is invalid")
    pointer = _attempt_pointer(pointer_path)
    if (
        pointer is None
        or pointer.get("status") != "sealed"
        or pointer.get("attempt_id") != expected_attempt_id
        or pointer.get("head") != expected_head
        or pointer.get("seal_id") != seal.get("seal_id")
        or pointer.get("seal_sha256") != _digest_bytes(_read_regular(seal_path))
    ):
        raise LifecycleError("operations seal disagrees with the sealed pointer")
    return seal


def as_plan_history_error(error: LifecycleError) -> PlanHistoryError:
    return PlanHistoryError(str(error))


__all__ = [
    "FinalGateOutputBinding",
    "LifecycleError",
    "abandon_final_attempt",
    "as_plan_history_error",
    "begin_final_attempt",
    "bind_final_gate_output",
    "load_bound_operations",
    "reopen_seal",
    "seal_attempt",
    "validate_current_pointer",
    "validate_seal",
    "write_bound_final_gate_output",
]
