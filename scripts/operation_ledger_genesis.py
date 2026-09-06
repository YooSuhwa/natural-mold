"""Pure sequence-zero contract for the operations ledger."""

from __future__ import annotations

from collections.abc import Mapping

from operation_ledger_format import (
    GENESIS_ARGUMENT_KEYS,
    HASH_PATTERN,
    LINK_KEYS,
    SHA_PATTERN,
    Clock,
    JSONValue,
    LedgerError,
    _timestamp,
    _with_hash,
)


def _validate_genesis_facts(
    facts: Mapping[str, JSONValue], *, require_key_order: bool = False
) -> None:
    if set(facts) != set(GENESIS_ARGUMENT_KEYS) or (
        require_key_order and list(facts) != GENESIS_ARGUMENT_KEYS
    ):
        raise LedgerError("bootstrap facts must use the exact ordered schema")
    base_sha = facts["base_sha"]
    if not isinstance(base_sha, str) or SHA_PATTERN.fullmatch(base_sha) is None:
        raise LedgerError("base SHA must be 40 lowercase hexadecimal characters")
    for key in ("expected_plan_sha", "source_plan_sha256"):
        value = facts[key]
        if not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None:
            raise LedgerError("plan hashes must be 64 lowercase hexadecimal characters")
    if facts["expected_plan_sha"] != facts["source_plan_sha256"]:
        raise LedgerError("source plan does not match the approved digest")
    if facts["tracked_status"] != "clean":
        raise LedgerError("tracked worktree must be clean")
    review = facts["expected_review_round"]
    if not isinstance(review, str) or not review.strip() or len(review) > 128:
        raise LedgerError("review round must be a bounded nonempty string")
    links = facts["resolved_links"]
    if not isinstance(links, list) or len(links) != 2:
        raise LedgerError("exactly two resolved links are required")
    expected_paths = ["backend/.env", "backend/data"]
    for link, logical_path in zip(links, expected_paths, strict=True):
        if not isinstance(link, dict) or set(link) != LINK_KEYS:
            raise LedgerError("resolved link schema is invalid")
        if link["logical_path"] != logical_path or link["link_kind"] != "symlink":
            raise LedgerError("resolved links must be ordered and symbolic")
        expected_kind = "file" if logical_path.endswith(".env") else "directory"
        digest = link["target_path_sha256"]
        if link["target_kind"] != expected_kind:
            raise LedgerError("resolved link target kind is invalid")
        if not isinstance(digest, str) or HASH_PATTERN.fullmatch(digest) is None:
            raise LedgerError("resolved target fingerprint is invalid")


def build_genesis(facts: Mapping[str, JSONValue], *, clock: Clock) -> dict[str, JSONValue]:
    """Build the exact sequence-zero entry after validating all facts."""
    _validate_genesis_facts(facts, require_key_order=True)
    return _with_hash(
        {
            "schema_version": 1,
            "sequence": 0,
            "previous_entry_hash": None,
            "timestamp_utc": _timestamp(clock),
            "task_id": "02",
            "action_class": "bootstrap",
            "arguments": dict(facts),
            "status": "passed",
        }
    )
