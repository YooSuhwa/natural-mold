"""Bounded, secret-free metadata for failed isolated E2E exports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final, Never, NotRequired, TypedDict

MAX_FAILURE_DIAGNOSTICS: Final = 16
MAX_NODE_ID_LENGTH: Final = 2048
MAX_ARTIFACT_PATH_LENGTH: Final = 1024
MAX_ARTIFACT_PATH_PARTS: Final = 33
MAX_SOURCE_LOCATION_VALUE: Final = 1_000_000
FAILURE_STATUSES: Final = frozenset({"failed", "timedOut", "interrupted"})
SECRET_SCAN_RULE_IDS: Final = frozenset(
    {
        "configured_exact_secret",
        "private_key",
        "bearer_token",
        "sensitive_assignment",
        "credential_dsn",
        "jwt",
        "credential_query",
        "structured_sensitive_value",
        "structured_cookie_value",
        "representation_bounds",
    }
)
SAFE_SPEC: Final = re.compile(r"e2e/[A-Za-z0-9][A-Za-z0-9._/-]*\.spec\.ts")
CONTROL_CHARACTER: Final = re.compile(r"[\x00-\x1f\x7f]")
SOURCE_KINDS: Final = frozenset({"results", "captures", "legacy-captures"})
SAFE_PATH_COMPONENT: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass(frozen=True, slots=True, order=True)
class FailureLocation:
    file: str
    line: int
    column: int


@dataclass(frozen=True, slots=True, order=True)
class FailureDiagnostic:
    node_id: str
    status: str
    location: FailureLocation | None = None


class FailureLocationPayload(TypedDict):
    file: str
    line: int
    column: int


class FailureDiagnosticPayload(TypedDict):
    node_id: str
    status: str
    location: NotRequired[FailureLocationPayload]


@dataclass(frozen=True, slots=True)
class SourceRejection:
    category: str
    rule_id: str
    artifact_path: str
    tests: tuple[FailureDiagnostic, ...]


class FailureDiagnosticError(ValueError):
    """Stable rejection that never reflects the untrusted metadata."""


def _fail() -> Never:
    raise FailureDiagnosticError("invalid_source_rejection")


def _safe_artifact_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        _fail()
    path = PurePosixPath(value)
    if (
        len(value) > MAX_ARTIFACT_PATH_LENGTH
        or not 2 <= len(path.parts) <= MAX_ARTIFACT_PATH_PARTS
        or path.as_posix() != value
        or path.parts[0] not in SOURCE_KINDS
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(SAFE_PATH_COMPONENT.fullmatch(part) is None for part in path.parts[1:])
    ):
        _fail()
    return value


def _safe_node_id(value: object, project: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_NODE_ID_LENGTH
        or CONTROL_CHARACTER.search(value) is not None
    ):
        _fail()
    prefix = f"{project}::"
    if not value.startswith(prefix):
        _fail()
    separator = value.find("::", len(prefix))
    if separator < 0 or separator == len(value) - 2:
        _fail()
    spec = value[len(prefix) : separator]
    if SAFE_SPEC.fullmatch(spec) is None or ".." in PurePosixPath(spec).parts:
        _fail()
    return value


def _node_spec(node_id: str, project: str) -> str:
    prefix = f"{project}::"
    separator = node_id.find("::", len(prefix))
    return node_id[len(prefix) : separator]


def _safe_location(value: object) -> FailureLocation:
    if not isinstance(value, dict) or set(value) != {"file", "line", "column"}:
        _fail()
    file = value.get("file")
    line = value.get("line")
    column = value.get("column")
    if (
        not isinstance(file, str)
        or len(file) > MAX_ARTIFACT_PATH_LENGTH
        or SAFE_SPEC.fullmatch(file) is None
        or ".." in PurePosixPath(file).parts
        or type(line) is not int
        or type(column) is not int
        or not 1 <= line <= MAX_SOURCE_LOCATION_VALUE
        or not 1 <= column <= MAX_SOURCE_LOCATION_VALUE
    ):
        _fail()
    return FailureLocation(file, line, column)


def parse_failure_diagnostics(value: object, project: str) -> tuple[FailureDiagnostic, ...]:
    """Parse the runner's bounded, source-only unexpected failure projection."""
    if not isinstance(value, list) or len(value) > MAX_FAILURE_DIAGNOSTICS:
        _fail()
    parsed: list[FailureDiagnostic] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) not in (
            {"node_id", "status"},
            {"node_id", "status", "location"},
        ):
            _fail()
        node_id = _safe_node_id(item.get("node_id"), project)
        status = item.get("status")
        if not isinstance(status, str) or status not in FAILURE_STATUSES or node_id in seen:
            _fail()
        seen.add(node_id)
        location = _safe_location(item.get("location")) if "location" in item else None
        if location is not None and location.file != _node_spec(node_id, project):
            _fail()
        parsed.append(FailureDiagnostic(node_id, status, location))
    return tuple(parsed)


def failure_diagnostics_payload(
    value: tuple[FailureDiagnostic, ...], project: str
) -> list[FailureDiagnosticPayload]:
    """Serialize only the allowlisted diagnostic fields."""
    if len(value) > MAX_FAILURE_DIAGNOSTICS:
        _fail()
    payload: list[FailureDiagnosticPayload] = []
    seen: set[str] = set()
    for item in value:
        node_id = _safe_node_id(item.node_id, project)
        if item.status not in FAILURE_STATUSES or node_id in seen:
            _fail()
        seen.add(node_id)
        diagnostic = FailureDiagnosticPayload(node_id=node_id, status=item.status)
        if item.location is not None:
            location = FailureLocationPayload(
                file=item.location.file,
                line=item.location.line,
                column=item.location.column,
            )
            _safe_location(location)
            if item.location.file != _node_spec(node_id, project):
                _fail()
            diagnostic["location"] = location
        payload.append(diagnostic)
    return payload


def parse_source_rejection(value: object, project: str) -> SourceRejection:
    if not isinstance(value, dict) or set(value) != {
        "category",
        "rule_id",
        "artifact_path",
        "tests",
    }:
        _fail()
    category = value.get("category")
    rule_id = value.get("rule_id")
    tests = value.get("tests")
    if (
        category != "secret_scan"
        or not isinstance(rule_id, str)
        or rule_id not in SECRET_SCAN_RULE_IDS
        or not isinstance(tests, list)
        or not 1 <= len(tests) <= MAX_FAILURE_DIAGNOSTICS
    ):
        _fail()
    parsed: list[FailureDiagnostic] = []
    seen: set[str] = set()
    for item in tests:
        if not isinstance(item, dict) or set(item) != {"node_id", "status"}:
            _fail()
        node_id = _safe_node_id(item.get("node_id"), project)
        status = item.get("status")
        if not isinstance(status, str) or status not in FAILURE_STATUSES or node_id in seen:
            _fail()
        seen.add(node_id)
        parsed.append(FailureDiagnostic(node_id, status))
    return SourceRejection(
        category,
        rule_id,
        _safe_artifact_path(value.get("artifact_path")),
        tuple(parsed),
    )


def source_rejection_payload(value: SourceRejection) -> dict[str, object]:
    return {
        "category": value.category,
        "rule_id": value.rule_id,
        "artifact_path": value.artifact_path,
        "tests": [{"node_id": item.node_id, "status": item.status} for item in value.tests],
    }
