from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from datetime import datetime
from typing import Final, TypeGuard

from scripts.task10_root_artifact_io import JsonValue

type JsonObject = dict[str, JsonValue]

HEX40: Final = re.compile(r"[0-9a-f]{40}")
HEX64: Final = re.compile(r"[0-9a-f]{64}")
UTC: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
ROLE_NAMES: Final = ("plan", "candidate", "evidence", "native_source", "cleanup")
REQUEST_FIXED: Final[dict[str, JsonValue]] = {
    "tool": "agents.spawn_agent",
    "agent_type": "lazycodex-code-reviewer",
    "role": "independent_implementation_reviewer",
    "fork_turns": "none",
    "model_id": "gpt-5.6-terra",
    "model_override": None,
    "launch_owner": "root_coordinator",
    "status": "registered_before_launch",
}
PROVENANCE_FIXED: Final[dict[str, JsonValue]] = {
    "tool": "agents.spawn_agent",
    "model_id": "gpt-5.6-terra",
    "status": "completed",
}
BINDING_KEYS: Final = {"schema_version", "kind", "candidate", "roles", "review_input"}
SEAL_KEYS: Final = {
    "schema_version",
    "kind",
    "candidate",
    "binding",
    "review_input",
    "freeze_argv",
}
REQUEST_KEYS: Final = {
    "schema_version",
    "kind",
    "tool",
    "task_name",
    "agent_type",
    "role",
    "fork_turns",
    "model_id",
    "model_override",
    "message_sha256",
    "request_text",
    "plan",
    "review_input",
    "binding",
    "binding_seal",
    "binding_sha256",
    "candidate",
    "launch_owner",
    "status",
}
PROVENANCE_KEYS: Final = {
    "schema_version",
    "kind",
    "request",
    "tool",
    "task_name",
    "agent_id",
    "session_id",
    "model_id",
    "launch_receipt",
    "binding_sha256",
    "candidate",
    "raw_report",
    "raw_sha256",
    "started_at_utc",
    "completed_at_utc",
    "status",
}


class ReviewMetadataError(ValueError):
    pass


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ReviewMetadataError(code)


def _is_object(value: JsonValue) -> TypeGuard[dict[str, JsonValue]]:
    return isinstance(value, dict)


def _closed(value: JsonValue, keys: Collection[str], kind: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ReviewMetadataError("review_document_schema")
    _require(set(value) == set(keys) and value.get("kind") == kind, "review_document_schema")
    _require(
        type(value.get("schema_version")) is int and value["schema_version"] == 1,
        "review_document_schema",
    )
    return value


def _candidate(value: JsonValue) -> str:
    if not isinstance(value, str) or HEX40.fullmatch(value) is None:
        raise ReviewMetadataError("review_candidate_invalid")
    return value


def _legacy(value: JsonValue, code: str) -> JsonObject:
    if not isinstance(value, dict) or set(value) != {"path", "sha256", "bytes"}:
        raise ReviewMetadataError(code)
    path, digest, size = value.get("path"), value.get("sha256"), value.get("bytes")
    valid = isinstance(path, str) and bool(path)
    valid = valid and isinstance(digest, str) and HEX64.fullmatch(digest) is not None
    valid = valid and type(size) is int and size >= 0
    _require(valid, code)
    return value


def validate_registered_argv(value: JsonValue) -> None:
    document = value if _is_object(value) else {}
    body = document.get("argv")
    valid = set(document) == {"manifest_kind", "cwd", "argv"}
    valid = valid and document.get("manifest_kind") == "argv_manifest"
    valid = valid and document.get("cwd") == "backend"
    if not valid or not isinstance(body, list) or not body:
        raise ReviewMetadataError("registered_argv_manifest_invalid")
    _require(all(isinstance(item, str) for item in body), "registered_argv_manifest_invalid")


def validate_candidate_document(value: JsonValue, candidate: str, root: str) -> None:
    document = value if _is_object(value) else {}
    source = document.get("source_proof", document.get("sources"))
    source_valid = (isinstance(source, str) and bool(source)) or (
        isinstance(source, dict) and bool(source)
    )
    _require(document.get("candidate") == _candidate(candidate), "candidate_manifest_invalid")
    _require(document.get("workspace_root") == root and source_valid, "candidate_manifest_invalid")


def _roles(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict) or set(value) != set(ROLE_NAMES):
        raise ReviewMetadataError("review_binding_roles")
    for role in ROLE_NAMES:
        _legacy(value[role], "review_binding_roles")
    return value


def _build(kind: str, candidate: str, **values: JsonValue) -> JsonObject:
    return {"schema_version": 1, "kind": kind, "candidate": _candidate(candidate)} | values


def build_review_binding(
    candidate: str,
    roles: Mapping[str, JsonValue],
    review_input: Mapping[str, JsonValue],
) -> JsonObject:
    _candidate(candidate)
    _roles(dict(roles))
    _legacy(dict(review_input), "review_binding_input")
    return _build(
        "task10-review-binding", candidate, roles=dict(roles), review_input=dict(review_input)
    )


def validate_review_binding(
    value: JsonValue,
    expected_candidate: str,
    expected_roles: Mapping[str, JsonValue],
    expected_review_input: Mapping[str, JsonValue],
) -> JsonObject:
    document = _closed(value, BINDING_KEYS, "task10-review-binding")
    _candidate(expected_candidate)
    _roles(document.get("roles"))
    _legacy(document.get("review_input"), "review_binding_input")
    valid = document.get("candidate") == expected_candidate
    valid = valid and document.get("roles") == dict(expected_roles)
    valid = valid and document.get("review_input") == dict(expected_review_input)
    _require(valid, "review_binding_mismatch")
    return document


def build_binding_seal(
    candidate: str,
    binding: Mapping[str, JsonValue],
    review_input: Mapping[str, JsonValue],
    freeze_argv: Mapping[str, JsonValue],
) -> JsonObject:
    _candidate(candidate)
    for value in (binding, review_input, freeze_argv):
        _legacy(dict(value), "review_binding_seal_invalid")
    return _build(
        "task10-review-binding-seal",
        candidate,
        binding=dict(binding),
        review_input=dict(review_input),
        freeze_argv=dict(freeze_argv),
    )


def validate_binding_seal(
    value: JsonValue,
    expected_candidate: str,
    expected_binding: Mapping[str, JsonValue],
    expected_review_input: Mapping[str, JsonValue],
    expected_freeze_argv: Mapping[str, JsonValue],
) -> JsonObject:
    document = _closed(value, SEAL_KEYS, "task10-review-binding-seal")
    expected = (expected_binding, expected_review_input, expected_freeze_argv)
    for item in (
        document.get("binding"),
        document.get("review_input"),
        document.get("freeze_argv"),
    ):
        _legacy(item, "review_binding_seal_invalid")
    valid = document.get("candidate") == _candidate(expected_candidate)
    valid = valid and document.get("binding") == dict(expected[0])
    valid = valid and document.get("review_input") == dict(expected[1])
    valid = valid and document.get("freeze_argv") == dict(expected[2])
    _require(valid, "review_binding_seal_invalid")
    return document


def _timestamp(value: JsonValue, code: str) -> datetime:
    if not isinstance(value, str) or UTC.fullmatch(value) is None:
        raise ReviewMetadataError(code)
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ReviewMetadataError(code) from exc


def validate_reviewer_exchange(
    request: JsonValue,
    provenance: JsonValue,
    expected_request: Mapping[str, JsonValue],
    expected_provenance: Mapping[str, JsonValue],
    request_text: str,
    expected_request_text: str,
    raw_sha256: str,
) -> None:
    """Validate request and completed provenance without reading or hashing bytes."""
    request_doc = _closed(request, REQUEST_KEYS, "task10-reviewer-request")
    provenance_doc = _closed(provenance, PROVENANCE_KEYS, "task10-reviewer-provenance")
    for key, expected in (REQUEST_FIXED | dict(expected_request)).items():
        _require(request_doc.get(key) == expected, "reviewer_request_invalid")
    for key, expected in (PROVENANCE_FIXED | dict(expected_provenance)).items():
        _require(provenance_doc.get(key) == expected, "reviewer_provenance_invalid")
    for field in ("request_text", "plan", "review_input", "binding", "binding_seal"):
        _legacy(request_doc.get(field), "reviewer_request_invalid")
    for field in ("request", "launch_receipt", "raw_report"):
        _legacy(provenance_doc.get(field), "reviewer_provenance_invalid")
    _require(request_text == expected_request_text, "reviewer_request_invalid")
    _require(provenance_doc.get("raw_sha256") == raw_sha256, "reviewer_provenance_invalid")
    _require(
        provenance_doc.get("task_name") == request_doc.get("task_name"),
        "reviewer_provenance_invalid",
    )
    started = _timestamp(provenance_doc.get("started_at_utc"), "reviewer_provenance_invalid")
    completed = _timestamp(provenance_doc.get("completed_at_utc"), "reviewer_provenance_invalid")
    _require(completed >= started, "reviewer_provenance_invalid")
    for field in ("task_name", "agent_id", "session_id"):
        value = provenance_doc.get(field) if field != "task_name" else request_doc.get(field)
        _require(isinstance(value, str) and bool(value), "reviewer_provenance_invalid")


def build_review_acceptance(
    candidate: str,
    verdict: str,
    binding: Mapping[str, JsonValue],
    binding_seal: Mapping[str, JsonValue],
    report: Mapping[str, JsonValue],
) -> JsonObject:
    """Build the specialized acceptance from parsed verdict and registered tuples."""
    _candidate(candidate)
    _require(verdict == "APPROVE", "review_verdict_invalid")
    for value in (binding, binding_seal, report):
        _legacy(dict(value), "review_acceptance_invalid")
    return {
        "status": "verified",
        "candidate": candidate,
        "verdict": verdict,
        "binding": dict(binding),
        "binding_seal": dict(binding_seal),
        "report": dict(report),
    }
