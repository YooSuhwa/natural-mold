"""Pure schema and lifecycle validation for Task 10 Root authority."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence

from scripts.task10_root_artifact_io import AuthorityArtifact, JsonValue
from scripts.task10_root_authority_contract import STAGE_SPECS

HEX40 = re.compile(r"[0-9a-f]{40}")
UNMERGED = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})


class AuthorityValidationError(ValueError): ...


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuthorityValidationError(message)


def strict_integer(value: JsonValue, expected: int | None = None) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and (expected is None or value == expected)
    )


def validate_sequences(record: JsonValue, seal: JsonValue, expected: int) -> int:
    require(strict_integer(record, expected), "record_seal_mismatch")
    require(strict_integer(seal, expected), "record_seal_mismatch")
    return expected


def exact(document: Mapping[str, JsonValue], keys: set[str], kind: str) -> None:
    require(set(document) == keys, f"{kind}_schema")
    version = document.get("schema_version")
    require(strict_integer(version, 1), f"{kind}_schema")
    require(document.get("kind") == kind, f"{kind}_schema")


def valid_git_xy(xy: str) -> bool:
    if xy == "??" or xy in UNMERGED:
        return True
    return xy != "  " and xy[0] in " MTADRC" and xy[1] in " MTDRC"


def validate_git_state(value: JsonValue) -> None:
    if not isinstance(value, dict) or set(value) != {"head", "entries", "canonical_paths"}:
        raise AuthorityValidationError("git_state_schema")
    head, entries, paths = value.get("head"), value.get("entries"), value.get("canonical_paths")
    require(isinstance(head, str) and HEX40.fullmatch(head) is not None, "git_state_schema")
    if not isinstance(entries, list) or not isinstance(paths, list):
        raise AuthorityValidationError("git_state_schema")
    observed: list[str] = []
    for row in entries:
        if not isinstance(row, dict) or set(row) != {"xy", "paths"}:
            raise AuthorityValidationError("git_state_schema")
        xy, names = row.get("xy"), row.get("paths")
        if not isinstance(xy, str) or not isinstance(names, list):
            raise AuthorityValidationError("git_state_schema")
        require(len(xy) == 2 and xy.isascii() and valid_git_xy(xy), "git_state_schema")
        expected = 2 if any(character in "RC" for character in xy) else 1
        require(len(names) == expected, "git_state_schema")
        for name in names:
            if not isinstance(name, str) or name == "":
                raise AuthorityValidationError("git_state_schema")
            observed.append(name)
    require(paths == sorted(set(observed)), "git_state_schema")


def _artifact(value: JsonValue, content_type: str | None = None) -> AuthorityArtifact:
    keys = {"path", "sha256", "bytes", "content_type"}
    if not isinstance(value, dict) or set(value) != keys:
        raise AuthorityValidationError("authority_tuple_schema")
    path, digest = value.get("path"), value.get("sha256")
    size, content = value.get("bytes"), value.get("content_type")
    if not isinstance(path, str) or path == "":
        raise AuthorityValidationError("authority_tuple_type")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise AuthorityValidationError("authority_tuple_type")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise AuthorityValidationError("authority_tuple_type")
    if content not in {"source", "metadata"}:
        raise AuthorityValidationError("authority_tuple_type")
    require(content_type is None or content == content_type, "authority_tuple_type")
    verified_content = "source" if content == "source" else "metadata"
    return {"path": path, "sha256": digest, "bytes": size, "content_type": verified_content}


def validate_record(
    record: Mapping[str, JsonValue],
    seal: Mapping[str, JsonValue],
    record_tuple: AuthorityArtifact,
    parent: AuthorityArtifact | None,
    sequence: int,
    workspace_root: str,
) -> str:
    common = {"schema_version", "kind", "record_id", "sequence", "stage", "status"}
    common |= {"workspace_root", "candidate", "publisher"}
    exact(record, common | {"git_state", "parent_seal", "roles"}, "task10-root-authority-record")
    exact(seal, common | {"record", "parent_seal"}, "task10-root-authority-seal")
    stage = record.get("stage")
    if not isinstance(stage, str) or stage not in STAGE_SPECS:
        raise AuthorityValidationError("authority_stage_unknown")
    spec = STAGE_SPECS[stage]
    shared = common - {"schema_version", "kind"}
    require(all(record.get(key) == seal.get(key) for key in shared), "record_seal_mismatch")
    registered_sequence = validate_sequences(record.get("sequence"), seal.get("sequence"), sequence)
    registered = (registered_sequence, record.get("status"), record.get("workspace_root"))
    require(registered == (sequence, spec.status, workspace_root), "record_seal_mismatch")
    require(record.get("parent_seal") == parent, "record_seal_mismatch")
    require(
        (seal.get("parent_seal"), seal.get("record")) == (parent, record_tuple),
        "record_seal_mismatch",
    )
    candidate = record.get("candidate")
    null_candidate = stage in {
        "I1_AUTHORITY_BASELINE",
        "I1_AUTHORITY_SCOPE_AMENDED",
        "I1_AUTHORITY_CAPACITY_AMENDED",
    }
    require(null_candidate == (candidate is None), "candidate_invalid")
    if candidate is not None:
        _artifact(candidate, "metadata")
    validate_git_state(record.get("git_state"))
    try:
        uuid.UUID(str(record.get("record_id")))
    except ValueError as exc:
        raise AuthorityValidationError("record_id_invalid") from exc
    roles = record.get("roles")
    if not isinstance(roles, list) or len(roles) != len(spec.roles):
        raise AuthorityValidationError("role_inventory_invalid")
    candidate_role = {
        "I1_POSTCOMMIT_CANDIDATE": "candidate_manifest",
        "SOURCE_STAGE_INPUT": "candidate",
    }.get(stage)
    for row, expected in zip(roles, spec.roles, strict=True):
        if not isinstance(row, dict) or set(row) != {"role", "artifact", "manifest_kind"}:
            raise AuthorityValidationError("role_inventory_invalid")
        artifact = _artifact(row.get("artifact"), expected.content_type)
        actual = (row.get("role"), artifact["content_type"], row.get("manifest_kind"))
        require(actual == expected, "role_inventory_invalid")
        if expected.name == candidate_role:
            require(candidate == artifact, "candidate_mismatch")
    publisher = record.get("publisher")
    fields = {"actor_role", "session_id", "provenance", "output_scope"}
    if not isinstance(publisher, dict) or set(publisher) != fields:
        raise AuthorityValidationError("publisher_invalid")
    scopes = {
        "root_coordinator": "actual_root",
        "fixture_registrar": "fixture_workspace/private_fixture_only",
    }
    actor, session = publisher.get("actor_role"), publisher.get("session_id")
    if not isinstance(actor, str) or actor not in scopes:
        raise AuthorityValidationError("publisher_invalid")
    require(isinstance(session, str) and bool(session), "publisher_invalid")
    require(publisher.get("output_scope") == scopes[actor], "publisher_scope_invalid")
    _artifact(publisher.get("provenance"), "metadata")
    return stage


def valid_transition(sequence: JsonValue, stage: str, previous: str | None) -> bool:
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        return False
    if sequence == 1:
        return stage == "I1_AUTHORITY_BASELINE" and previous is None
    if sequence == 2:
        return stage == previous == "I1_AUTHORITY_BASELINE"
    if sequence == 3:
        return stage == "I1_AUTHORITY_SCOPE_AMENDED" and previous == "I1_AUTHORITY_BASELINE"
    if sequence == 4:
        return stage == "I1_AUTHORITY_CAPACITY_AMENDED" and previous == "I1_AUTHORITY_SCOPE_AMENDED"
    if stage in {
        "I1_AUTHORITY_BASELINE",
        "I1_AUTHORITY_SCOPE_AMENDED",
        "I1_AUTHORITY_CAPACITY_AMENDED",
    }:
        return False
    names = tuple(STAGE_SPECS)
    prior = names[names.index(stage) - 1]
    if stage == "SOURCE_STAGE_INPUT":
        return previous in {"I1_ACCEPTED", "SOURCE_STAGE_ACCEPTED"}
    return previous == prior


def validate_release_overlay(
    current: Mapping[str, JsonValue], previous: Mapping[str, JsonValue]
) -> None:
    current_roles, previous_roles = current.get("roles"), previous.get("roles")
    if not isinstance(current_roles, list) or not isinstance(previous_roles, list):
        raise AuthorityValidationError("release_overlay_invalid")
    require(len(current_roles) == len(previous_roles) == 14, "release_overlay_invalid")
    require(current_roles[1:] == previous_roles[1:], "release_overlay_invalid")
    current_release, previous_release = current_roles[0], previous_roles[0]
    if not isinstance(current_release, dict) or not isinstance(previous_release, dict):
        raise AuthorityValidationError("release_overlay_invalid")
    require(
        current_release.get("role") == previous_release.get("role") == "exact_path_release",
        "release_overlay_invalid",
    )
    require(
        current_release.get("artifact") != previous_release.get("artifact"),
        "release_overlay_invalid",
    )


def validate_chain_step(
    record: Mapping[str, JsonValue], previous: Mapping[str, JsonValue] | None, sequence: int
) -> None:
    stage = record.get("stage")
    prior = previous.get("stage") if previous is not None else None
    if not isinstance(stage, str) or (prior is not None and not isinstance(prior, str)):
        raise AuthorityValidationError("authority_stage_order")
    require(valid_transition(sequence, stage, prior), "authority_stage_order")
    if sequence in {2, 3, 4}:
        if previous is None:
            raise AuthorityValidationError("authority_stage_order")
        validate_release_overlay(record, previous)
    if stage == "SOURCE_STAGE_ACCEPTED":
        if previous is None:
            raise AuthorityValidationError("authority_stage_order")
        require(record.get("candidate") == previous.get("candidate"), "candidate_mismatch")


def select_occurrence(
    records: Sequence[Mapping[str, JsonValue]],
    stage: str,
    candidate: Mapping[str, JsonValue] | None = None,
    parent_seal: Mapping[str, JsonValue] | None = None,
) -> Mapping[str, JsonValue]:
    for record in reversed(records):
        matches = record.get("stage") == stage
        matches &= candidate is None or record.get("candidate") == candidate
        matches &= parent_seal is None or record.get("parent_seal") == parent_seal
        if matches:
            return record
    raise AuthorityValidationError("authority_occurrence_not_registered")
