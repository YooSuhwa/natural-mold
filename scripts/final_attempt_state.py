"""Pointer, journal, and immutable-state validation for final attempts."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from final_attempt_evidence import _inventory, _validate_inventory
from final_attempt_io import (
    SHA40,
    SHA64,
    CrashHook,
    LifecycleError,
    _atomic_json,
    _digest_json,
    _locked_parent,
    _read_json,
    _read_optional,
)
from operation_ledger_format import JSONValue
from operation_ledger_writer import EvidenceWriterLock, current_evidence_lock
from plan_history_contract import PlanContract

PHASES: Final = {
    "begin": (
        "prepared",
        "ledger_appended",
        "directory_created",
        "pointer_open",
        "committed",
    ),
    "abandon": (
        "prepared",
        "ledger_appended",
        "terminal_written",
        "pointer_abandoned",
        "committed",
    ),
    "seal": ("prepared", "seal_written", "pointer_sealed", "committed"),
    "reopen": ("prepared", "ledger_appended", "pointer_reopened", "committed"),
}


def _advance(
    journal_path: Path,
    journal: dict[str, JSONValue],
    phase: str,
    hook: CrashHook | None,
    evidence_lock: EvidenceWriterLock,
) -> None:
    updated = dict(journal)
    updated["phase"] = phase
    _atomic_json(journal_path, updated, hook, f"journal:{phase}", evidence_lock)
    journal.clear()
    journal.update(updated)


def _validate_contract(contract: PlanContract, plan_sha: str, review_round: str) -> None:
    if contract.plan_sha256 != plan_sha or contract.review_round != review_round:
        raise LifecycleError("explicit lifecycle plan identity disagrees with tracked contract")


def _validate_journal_plan(
    journal: Mapping[str, JSONValue], plan_sha: str, review_round: str
) -> None:
    inputs = journal.get("inputs")
    if (
        not isinstance(inputs, dict)
        or inputs.get("plan_sha256") != plan_sha
        or inputs.get("review_round") != review_round
    ):
        raise LifecycleError("pending lifecycle journal belongs to another plan identity")


def _validate_layout(
    operations: Path,
    pointer_path: Path,
    journal_root: Path,
    attempt_root: Path,
) -> None:
    evidence_root = operations.absolute().parent
    if (
        pointer_path.absolute() != evidence_root / "current-final-attempt.json"
        or journal_root.absolute() != evidence_root / "lifecycle-journals"
        or attempt_root.absolute() != evidence_root / "final-attempts"
    ):
        raise LifecycleError("lifecycle paths must use the fixed evidence layout")


def _safe_relative(repo_root: Path, path: Path) -> str:
    try:
        return path.absolute().relative_to(repo_root.absolute()).as_posix()
    except ValueError as error:
        raise LifecycleError("lifecycle path escapes the repository") from error


def _attempt_pointer(pointer_path: Path) -> dict[str, JSONValue] | None:
    pointer = _read_optional(pointer_path)
    if pointer is None:
        return None
    base = {"schema_version", "attempt_id", "attempt_dir", "status", "head"}
    extras = {
        "abandoned": {"transition_id", "frozen_inventory"},
        "reopened": {"transition_id", "frozen_inventory", "seal_id", "seal_sha256"},
        "sealed": {"seal_id", "seal_sha256"},
        "open": set(),
    }
    status = pointer.get("status")
    if status not in extras or set(pointer) != base | extras[str(status)]:
        raise LifecycleError("current final-attempt pointer schema is invalid")
    if (
        pointer.get("schema_version") != 1
        or not isinstance(pointer.get("attempt_id"), str)
        or SHA64.fullmatch(str(pointer["attempt_id"])) is None
        or not isinstance(pointer.get("attempt_dir"), str)
        or Path(str(pointer["attempt_dir"])).is_absolute()
        or ".." in Path(str(pointer["attempt_dir"])).parts
        or not isinstance(pointer.get("head"), str)
        or SHA40.fullmatch(str(pointer["head"])) is None
    ):
        raise LifecycleError("current final-attempt pointer values are invalid")
    return pointer


def validate_current_pointer(
    *, repo_root: Path, pointer_path: Path, attempt_root: Path
) -> dict[str, JSONValue]:
    pointer = _attempt_pointer(pointer_path)
    if pointer is None:
        raise LifecycleError("current final-attempt pointer is missing")
    attempt_dir = repo_root / str(pointer["attempt_dir"])
    if (
        attempt_dir.parent.absolute() != attempt_root.absolute()
        or attempt_dir.name != pointer["attempt_id"]
    ):
        raise LifecycleError("current pointer escapes or mismatches the attempt namespace")
    return pointer


def _journal_candidates(root: Path) -> list[tuple[Path, dict[str, JSONValue]]]:
    candidates: list[tuple[Path, dict[str, JSONValue]]] = []
    active_lock = current_evidence_lock()
    if active_lock is None:
        paths = sorted(root.glob("*.json"))
    else:
        descriptor, _ = _locked_parent(
            active_lock, root.absolute() / ".journal-anchor", create=False
        )
        try:
            paths = [
                root / name
                for name in sorted(os.listdir(descriptor))  # noqa: PTH208 - bound dirfd
                if name.endswith(".json")
            ]
        finally:
            os.close(descriptor)
    for path in paths:
        journal = _read_json(path)
        transition = journal.get("transition")
        phase = journal.get("phase")
        if (
            transition not in PHASES
            or not isinstance(phase, str)
            or phase not in PHASES[transition]
        ):
            raise LifecycleError("lifecycle journal transition or phase is impossible")
        required = {
            "schema_version",
            "transition",
            "transition_id",
            "phase",
            "inputs",
        }
        optional = (
            {"prior_pointer_sha256"}
            if transition == "begin"
            else {
                "pointer_sha256",
                "attempt_inventory",
                "external_exports",
            }
        )
        if transition == "seal":
            optional.update({"operations_sha256", "prerequisite_attestation"})
        if set(journal) != required | optional or journal.get("schema_version") != 1:
            raise LifecycleError("lifecycle journal schema is invalid")
        if phase != "committed":
            candidates.append((path, journal))
    if len(candidates) > 1:
        raise LifecycleError("multiple nonterminal lifecycle journals exist")
    return candidates


def _assert_prior_frozen(repo_root: Path, pointer: dict[str, JSONValue]) -> None:
    status = pointer.get("status")
    attempt_dir_raw = pointer.get("attempt_dir")
    if not isinstance(attempt_dir_raw, str):
        raise LifecycleError("prior pointer attempt directory is invalid")
    attempt_dir = repo_root / attempt_dir_raw
    match status:
        case "abandoned":
            marker = _read_json(attempt_dir / "attempt-abandoned.json")
            current = _inventory(attempt_dir)
            marker_free = {
                "files": [
                    item
                    for item in current["files"]
                    if isinstance(item, dict) and item.get("path") != "attempt-abandoned.json"
                ]
            }
            marker_free["sha256"] = _digest_json({"files": marker_free["files"]})
            if marker_free != marker.get("attempt_inventory"):
                raise LifecycleError("abandoned attempt inventory changed")
            _validate_inventory(attempt_dir, pointer.get("frozen_inventory"))
        case "reopened":
            inventory = pointer.get("frozen_inventory")
            _validate_inventory(attempt_dir, inventory)
        case "open" | "sealed":
            raise LifecycleError(f"cannot begin while pointer is {status}")
        case _:
            raise LifecycleError("prior pointer status is invalid")
