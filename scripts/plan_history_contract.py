"""Pure parsing and validation for the project-restart commit/receipt contract."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from operation_ledger_chain import _verify_bytes
from operation_ledger_format import JSONValue, LedgerError
from operation_ledger_fs import close, open_existing, open_parent, read_all

SHA40: Final = re.compile(r"[0-9a-f]{40}\Z")
SHA64: Final = re.compile(r"[0-9a-f]{64}\Z")
PRIMARY_TRAILER: Final = re.compile(r"^Plan-Item: (\d{2})$", re.MULTILINE)
FIX_TRAILER: Final = re.compile(r"^Plan-Fix: (\d{2})$", re.MULTILINE)
REPAIR_TRAILER: Final = re.compile(r"^Plan-Repair: (\d{2})$", re.MULTILINE)
INVALIDATES_TRAILER: Final = re.compile(r"^Invalidates-Receipt: ([0-9a-f]{64})$", re.MULTILINE)
MANDATED_PRIMARY_SEQUENCE: Final = (
    "02",
    "01",
    *(f"{value:02d}" for value in range(3, 26)),
)


class PlanHistoryError(RuntimeError):
    """The tracked plan, Git history, or evidence receipts disagree."""


@dataclass(frozen=True, slots=True)
class LedgerBinding:
    sequence: int
    entry_hash: str


@dataclass(frozen=True, slots=True)
class LegacyException:
    commit_sha: str
    parent_sha: str
    tree_sha: str
    message_sha256: str
    trailer: str
    owner: str
    invalidates_receipt: str | None
    receiptless: bool
    ledger_bindings: tuple[LedgerBinding, ...]


@dataclass(frozen=True, slots=True)
class PlanContract:
    base_sha: str
    plan_sha256: str
    review_round: str
    primary_sequence: tuple[str, ...]
    legacy_exceptions: tuple[LegacyException, ...]


@dataclass(frozen=True, slots=True)
class CommitRecord:
    sha: str
    parents: tuple[str, ...]
    tree_sha: str
    message: str

    @property
    def message_sha256(self) -> str:
        return hashlib.sha256((self.message.rstrip("\n") + "\n").encode()).hexdigest()


def _json_object(path: Path) -> dict[str, JSONValue]:
    parent_descriptor = -1
    descriptor = -1
    try:
        parent_descriptor, name = open_parent(path.absolute(), create=False)
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
        ):
            raise PlanHistoryError(f"JSON file identity is unsafe: {path}")
        chunks: list[bytes] = []
        size = 0
        while chunk := os.read(descriptor, min(1024 * 1024, 16 * 1024 * 1024 + 1 - size)):
            size += len(chunk)
            if size > 16 * 1024 * 1024:
                raise PlanHistoryError(f"JSON file is too large: {path}")
            chunks.append(chunk)
        named = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            (named.st_dev, named.st_ino) != (metadata.st_dev, metadata.st_ino)
            or named.st_uid != os.geteuid()
            or named.st_nlink != 1
            or not stat.S_ISREG(named.st_mode)
            or named.st_mode & 0o022
        ):
            raise PlanHistoryError(f"JSON file identity changed: {path}")
        value = json.loads(b"".join(chunks))
    except (OSError, LedgerError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PlanHistoryError(f"cannot read JSON object: {path}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)
    if not isinstance(value, dict):
        raise PlanHistoryError(f"JSON root must be an object: {path}")
    return value


def _string(value: JSONValue | None, field: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise PlanHistoryError(f"{field} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise PlanHistoryError(f"{field} has an invalid digest")
    return value


def load_contract(path: Path) -> PlanContract:
    raw = _json_object(path)
    if set(raw) != {
        "schema_version",
        "base_sha",
        "plan_sha256",
        "review_round",
        "primary_sequence",
        "legacy_exceptions",
    }:
        raise PlanHistoryError("plan contract schema keys are invalid")
    if raw.get("schema_version") != 1:
        raise PlanHistoryError("unsupported plan contract schema")
    sequence = raw.get("primary_sequence")
    exceptions = raw.get("legacy_exceptions")
    if not isinstance(sequence, list) or not isinstance(exceptions, list):
        raise PlanHistoryError("plan contract sequence or exceptions are invalid")
    primary = tuple(_string(item, "primary_sequence item") for item in sequence)
    if primary != MANDATED_PRIMARY_SEQUENCE:
        raise PlanHistoryError("plan contract primary sequence is not the immutable mandated order")
    parsed_exceptions: list[LegacyException] = []
    for raw_exception in exceptions:
        if not isinstance(raw_exception, dict):
            raise PlanHistoryError("legacy exception must be an object")
        exception_keys = {
            "commit_sha",
            "parent_sha",
            "tree_sha",
            "message_sha256",
            "trailer",
            "owner",
            "receiptless",
            "ledger_bindings",
        }
        if raw_exception.get("receiptless") is False:
            exception_keys.add("invalidates_receipt")
        if set(raw_exception) != exception_keys:
            raise PlanHistoryError("legacy exception schema keys are invalid")
        bindings_raw = raw_exception.get("ledger_bindings")
        if not isinstance(bindings_raw, list) or not bindings_raw:
            raise PlanHistoryError("legacy exception requires ledger bindings")
        bindings: list[LedgerBinding] = []
        for raw_binding in bindings_raw:
            if not isinstance(raw_binding, dict):
                raise PlanHistoryError("legacy ledger binding must be an object")
            if set(raw_binding) != {"sequence", "entry_hash"}:
                raise PlanHistoryError("legacy ledger binding schema keys are invalid")
            sequence_value = raw_binding.get("sequence")
            if (
                not isinstance(sequence_value, int)
                or isinstance(sequence_value, bool)
                or sequence_value < 0
            ):
                raise PlanHistoryError("legacy ledger sequence is invalid")
            bindings.append(
                LedgerBinding(
                    sequence=sequence_value,
                    entry_hash=_string(raw_binding.get("entry_hash"), "entry_hash", SHA64),
                )
            )
        invalidates_raw = raw_exception.get("invalidates_receipt")
        invalidates = (
            None
            if invalidates_raw is None
            else _string(invalidates_raw, "invalidates_receipt", SHA64)
        )
        receiptless = raw_exception.get("receiptless")
        if not isinstance(receiptless, bool) or receiptless != (invalidates is None):
            raise PlanHistoryError("legacy receiptless declaration is inconsistent")
        parsed_exceptions.append(
            LegacyException(
                commit_sha=_string(raw_exception.get("commit_sha"), "commit_sha", SHA40),
                parent_sha=_string(raw_exception.get("parent_sha"), "parent_sha", SHA40),
                tree_sha=_string(raw_exception.get("tree_sha"), "tree_sha", SHA40),
                message_sha256=_string(
                    raw_exception.get("message_sha256"), "message_sha256", SHA64
                ),
                trailer=_string(raw_exception.get("trailer"), "trailer"),
                owner=_string(raw_exception.get("owner"), "owner"),
                invalidates_receipt=invalidates,
                receiptless=receiptless,
                ledger_bindings=tuple(bindings),
            )
        )
    if len(parsed_exceptions) != 3 or len({item.commit_sha for item in parsed_exceptions}) != 3:
        raise PlanHistoryError("exactly three SHA-bound legacy exceptions are required")
    return PlanContract(
        base_sha=_string(raw.get("base_sha"), "base_sha", SHA40),
        plan_sha256=_string(raw.get("plan_sha256"), "plan_sha256", SHA64),
        review_round=_string(raw.get("review_round"), "review_round"),
        primary_sequence=primary,
        legacy_exceptions=tuple(parsed_exceptions),
    )


def read_git_history(
    repo_root: Path, base_sha: str, head: str = "HEAD"
) -> tuple[CommitRecord, ...]:
    if SHA40.fullmatch(base_sha) is None or (head != "HEAD" and SHA40.fullmatch(head) is None):
        raise PlanHistoryError("git history revisions are invalid")
    result = subprocess.run(  # noqa: S603 - revisions are strict lowercase SHA values or HEAD
        [
            "/usr/bin/git",
            "log",
            "--reverse",
            "--format=%H%x00%P%x00%T%x00%B%x00%x1e",
            f"{base_sha}..{head}",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    records: list[CommitRecord] = []
    for chunk in result.stdout.split("\x1e"):
        fields = chunk.strip("\n\x00").split("\x00", 3)
        if not fields or fields == [""]:
            continue
        if len(fields) != 4:
            raise PlanHistoryError("git history record is malformed")
        sha, parents, tree_sha, message = fields
        records.append(CommitRecord(sha, tuple(parents.split()), tree_sha, message))
    return tuple(records)


def _matching_ledger_entries(
    entries: list[dict[str, JSONValue]], commit_sha: str
) -> list[dict[str, JSONValue]]:
    return [
        entry
        for entry in entries
        if isinstance(entry.get("arguments"), dict)
        and (
            entry["arguments"].get("commit_sha") == commit_sha
            or entry["arguments"].get("commit") == commit_sha
        )
    ]


def _is_owner_task(task_id: JSONValue | None, owner: str) -> bool:
    return (
        isinstance(task_id, str)
        and re.search(rf"(?:^|\D){re.escape(owner)}(?:\D|$)", task_id) is not None
    )


def _commit_receipts(
    entries: list[dict[str, JSONValue]], commit_sha: str, owner: str, *, require_owner: bool = True
) -> list[dict[str, JSONValue]]:
    candidates = _matching_ledger_entries(entries, commit_sha)
    return [
        entry
        for entry in candidates
        if entry.get("status") == "passed"
        and (not require_owner or _is_owner_task(entry.get("task_id"), owner))
        and isinstance(entry.get("action_class"), str)
        and (
            entry["action_class"] == "commit"
            or "commit" in entry["action_class"].split("_")
            or "committed" in entry["action_class"].split("_")
            or (
                commit_sha == "a42a33c55a8de2bf8f4f239eb33695487f69cac0"
                and entry["action_class"] == "probe_receipts"
            )
        )
    ]


def _validate_legacy(
    commit: CommitRecord,
    exception: LegacyException,
    entries: list[dict[str, JSONValue]],
) -> str:
    if (
        commit.parents != (exception.parent_sha,)
        or commit.tree_sha != exception.tree_sha
        or commit.message_sha256 != exception.message_sha256
        or exception.trailer not in commit.message.splitlines()
    ):
        raise PlanHistoryError(f"legacy exception identity mismatch: {commit.sha}")
    for binding in exception.ledger_bindings:
        if (
            binding.sequence >= len(entries)
            or entries[binding.sequence].get("entry_hash") != binding.entry_hash
        ):
            raise PlanHistoryError(f"legacy exception ledger binding mismatch: {commit.sha}")
    if not _matching_ledger_entries(entries, commit.sha):
        raise PlanHistoryError(f"legacy exception commit is absent from the ledger: {commit.sha}")
    return exception.owner


def validate_history(
    contract: PlanContract,
    commits: tuple[CommitRecord, ...],
    entries: list[dict[str, JSONValue]],
) -> dict[str, JSONValue]:
    legacy = {item.commit_sha: item for item in contract.legacy_exceptions}
    observed_primary: list[str] = []
    repairs: list[dict[str, JSONValue]] = []
    for commit in commits:
        exception = legacy.get(commit.sha)
        if exception is not None:
            owner = _validate_legacy(commit, exception, entries)
            repairs.append({"commit_sha": commit.sha, "owner": owner, "legacy": True})
            continue
        primary = PRIMARY_TRAILER.findall(commit.message)
        fixes = FIX_TRAILER.findall(commit.message)
        repairs_legacy = REPAIR_TRAILER.findall(commit.message)
        trailer_count = len(primary) + len(fixes) + len(repairs_legacy)
        if trailer_count != 1:
            raise PlanHistoryError(f"commit requires exactly one plan trailer: {commit.sha}")
        if primary:
            observed_primary.append(primary[0])
            if not _commit_receipts(entries, commit.sha, primary[0]):
                raise PlanHistoryError(f"primary commit receipt binding is missing: {commit.sha}")
            continue
        if repairs_legacy or not fixes:
            raise PlanHistoryError(f"unmapped legacy or malformed repair commit: {commit.sha}")
        owner = fixes[0]
        if owner not in contract.primary_sequence:
            raise PlanHistoryError(f"invalid Plan-Fix owner: {commit.sha}")
        invalidated = INVALIDATES_TRAILER.findall(commit.message)
        if len(invalidated) != 1:
            raise PlanHistoryError(f"Plan-Fix requires one invalidated receipt: {commit.sha}")
        matching = _commit_receipts(entries, commit.sha, owner, require_owner=False)
        receipt_keys = (
            "invalidated_receipt",
            "invalidated_manifest_sha256",
            "invalidates_receipt",
        )
        if not any(
            isinstance(item.get("arguments"), dict)
            and any(item["arguments"].get(key) == invalidated[0] for key in receipt_keys)
            for item in matching
        ):
            raise PlanHistoryError(f"Plan-Fix receipt is not ledger-bound: {commit.sha}")
        repairs.append({"commit_sha": commit.sha, "owner": owner, "legacy": False})
    if tuple(observed_primary) != contract.primary_sequence:
        raise PlanHistoryError("primary Plan-Item sequence is missing, duplicated, or out of order")
    return {
        "primary_sequence": list(observed_primary),
        "primary_count": len(observed_primary),
        "repairs": repairs,
    }


def validate_plan_identity(
    contract: PlanContract,
    *,
    expected_plan_sha: str,
    expected_review_round: str,
    receipt_path: Path,
) -> dict[str, JSONValue]:
    receipt = _json_object(receipt_path)
    source = receipt.get("source")
    if not isinstance(source, dict):
        raise PlanHistoryError("precondition receipt source is missing")
    identities = (
        contract.plan_sha256,
        expected_plan_sha,
        source.get("plan_sha256"),
    )
    rounds = (contract.review_round, expected_review_round, source.get("review_round"))
    if len(set(identities)) != 1 or len(set(rounds)) != 1:
        raise PlanHistoryError("tracked, explicit, and receipt plan identities disagree")
    if source.get("base_sha") != contract.base_sha:
        raise PlanHistoryError("precondition receipt base SHA disagrees with the contract")
    return {"plan_sha256": contract.plan_sha256, "review_round": contract.review_round}


def load_verified_operations(path: Path) -> list[dict[str, JSONValue]]:
    bound = open_existing(path.absolute())
    try:
        return _verify_bytes(read_all(bound))
    finally:
        close(bound)
