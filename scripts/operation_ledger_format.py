"""Pure wire-format primitives for the operations ledger."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Final

type JSONValue = None | bool | int | str | list[JSONValue] | dict[str, JSONValue]
type Clock = Callable[[], datetime]
type InterruptionHook = Callable[[str], None]

ENTRY_KEYS: Final = {
    "schema_version",
    "sequence",
    "previous_entry_hash",
    "timestamp_utc",
    "task_id",
    "action_class",
    "arguments",
    "status",
    "entry_hash",
}
GENESIS_ARGUMENT_KEYS: Final = [
    "base_sha",
    "tracked_status",
    "expected_plan_sha",
    "expected_review_round",
    "source_plan_sha256",
    "resolved_links",
]
LINK_KEYS: Final = {"logical_path", "link_kind", "target_kind", "target_path_sha256"}
HASH_PATTERN: Final = re.compile(r"[0-9a-f]{64}\Z")
SHA_PATTERN: Final = re.compile(r"[0-9a-f]{40}\Z")
TIME_PATTERN: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
IDENTIFIER_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")
SENSITIVE_KEY_PATTERN: Final = re.compile(
    r"(?:api[_-]?key|secret|token|password|credential|cookie|authorization|headers?|env|request[_-]?body)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_PATTERN: Final = re.compile(
    r"(?:\A\s*(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+\s*\Z|"
    r"\b(?:api[_-]?key|secret|token|password|credential)\s*[:=]\s*[^\s,;]+)",
    re.IGNORECASE,
)
AUTHORIZATION_VALUE_PATTERN: Final = re.compile(
    r"(?:proxy-)?authorization\s*[:=]\s*(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+",
    re.IGNORECASE,
)
PRIVATE_KEY_PATTERN: Final = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    re.IGNORECASE,
)
PATH_ASSIGNMENT_PATTERN: Final = re.compile(
    r"\b(?:path|dir|file|root)\s*[:=]\s*(\S+)", re.IGNORECASE
)
WINDOWS_ABSOLUTE_PATTERN: Final = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|//)")
MAX_ARGUMENT_BYTES: Final = 16_384


class LedgerError(RuntimeError):
    """The ledger contract was violated."""


class InjectedInterruption(RuntimeError):
    """A test interrupted an append at an explicit durability boundary."""


def canonical_line(value: Mapping[str, JSONValue]) -> bytes:
    """Return canonical UTF-8 JSON with exactly one trailing LF."""
    _validate_json_value(value)
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def _validate_json_value(value: JSONValue | Mapping[str, JSONValue]) -> None:
    match value:
        case None | bool() | int() | str():
            return
        case float():
            raise LedgerError("floating-point values are forbidden")
        case list():
            for item in value:
                _validate_json_value(item)
        case dict():
            for key, item in value.items():
                if not isinstance(key, str):
                    raise LedgerError("JSON object keys must be strings")
                _validate_json_value(item)
        case _:
            raise LedgerError("unsupported JSON value")


def _timestamp(clock: Clock) -> str:
    sampled = clock().astimezone(UTC).replace(microsecond=0)
    return sampled.strftime("%Y-%m-%dT%H:%M:%SZ")


def _with_hash(entry: dict[str, JSONValue]) -> dict[str, JSONValue]:
    result = dict(entry)
    result["entry_hash"] = hashlib.sha256(canonical_line(entry)).hexdigest()
    return result


def _validate_safe_arguments(arguments: Mapping[str, JSONValue]) -> None:
    encoded = canonical_line(arguments)
    if len(encoded) > MAX_ARGUMENT_BYTES:
        raise LedgerError("operation arguments exceed the bounded schema")

    def unsafe_path(value: str) -> bool:
        components = re.split(r"[\\/]", value)
        return (
            value.startswith(("/", "~/"))
            or WINDOWS_ABSOLUTE_PATTERN.match(value) is not None
            or ".." in components
        )

    def visit(value: JSONValue, key: str = "") -> None:
        if SENSITIVE_KEY_PATTERN.search(key):
            raise LedgerError("secret-bearing argument keys are forbidden")
        match value:
            case str() if SENSITIVE_VALUE_PATTERN.search(value):
                raise LedgerError("secret-bearing argument values are forbidden")
            case str() if AUTHORIZATION_VALUE_PATTERN.search(value):
                raise LedgerError("authorization-bearing argument values are forbidden")
            case str() if PRIVATE_KEY_PATTERN.search(value):
                raise LedgerError("private-key argument values are forbidden")
            case str() if unsafe_path(value):
                raise LedgerError("resolved paths are forbidden")
            case str():
                assignment = PATH_ASSIGNMENT_PATTERN.search(value)
                if assignment is not None and unsafe_path(assignment.group(1)):
                    raise LedgerError("path assignment values are forbidden")
            case list():
                for item in value:
                    visit(item, key)
            case dict():
                for child_key, item in value.items():
                    visit(item, child_key)
            case _:
                _validate_json_value(value)

    visit(dict(arguments))
