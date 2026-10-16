"""Independent private authority scenarios for Task 10 consumers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tests.architecture.task10_authority_fixtures import (
    CONTROL,
    JsonValue,
    PrivateAuthority,
    append_private_stage,
    fixture_head,
    legacy_artifact,
    register_private_baseline,
    register_private_scope,
    replace_private_role,
    write_fixture_artifact,
)
from tests.architecture.task10_authority_literal_catalog import STAGES, RoleLiteral

GUARDED_FIELDS = (
    "imports",
    "exports",
    "routes",
    "locks",
    "lock_edges",
    "with_for_update",
    "hydration_aliases",
)
STRUCTURAL_KEYS = (
    "schema_version",
    "manifest_kind",
    "lane_id",
    "candidate",
    "baseline",
    "candidate_contract",
    "approved_paths",
    "moves",
    "ordered_blocks",
    "facades",
    "expected_guarded",
)


class AuthorityScenarioError(ValueError): ...


@dataclass(frozen=True, slots=True)
class StructuralCase:
    authority: PrivateAuthority
    baseline: Path
    candidate: Path
    contract: Path
    candidate_tuple: Mapping[str, JsonValue]


def _stage(name: str) -> tuple[str, tuple[RoleLiteral, ...]]:
    for stage_name, status, roles in STAGES:
        if stage_name == name:
            return status, roles
    raise AuthorityScenarioError(name)


def _value(role: RoleLiteral, label: str) -> Mapping[str, JsonValue]:
    if role.manifest_kind is not None:
        return {"manifest_kind": role.manifest_kind, "fixture_role": role.name, "label": label}
    return {"fixture_role": role.name, "label": label}


def advance_authority(
    previous: PrivateAuthority,
    stage: str,
    *,
    label: str | None = None,
    candidate: Mapping[str, JsonValue] | None = None,
    role_artifacts: Mapping[str, Mapping[str, JsonValue]] | None = None,
) -> PrivateAuthority:
    status, definitions = _stage(stage)
    stem = label or stage.lower()
    roles: list[dict[str, JsonValue]] = []
    paths: dict[str, Path] = {}
    for index, definition in enumerate(definitions, 1):
        relative = f"{CONTROL}/fixture-inputs/{stem}/{index:02d}-{definition.name}.json"
        artifact = (
            role_artifacts[definition.name]
            if role_artifacts is not None and definition.name in role_artifacts
            else write_fixture_artifact(
                previous.root, relative, _value(definition, stem), definition.content_type
            )
        )
        role = {"role": definition.name, "artifact": artifact}
        role["manifest_kind"] = definition.manifest_kind
        roles.append(role)
        artifact_path = artifact.get("path")
        if not isinstance(artifact_path, str):
            raise AuthorityScenarioError("fixture_artifact_path_invalid")
        paths[definition.name] = previous.root / artifact_path
    null_candidate = stage in {
        "I1_AUTHORITY_BASELINE",
        "I1_AUTHORITY_SCOPE_AMENDED",
        "I1_AUTHORITY_CAPACITY_AMENDED",
    }
    if candidate is None and not null_candidate:
        prior = json.loads(previous.record.read_text()).get("candidate")
        candidate = prior if isinstance(prior, dict) else None
    candidate_role = {
        "I1_POSTCOMMIT_CANDIDATE": "candidate_manifest",
        "SOURCE_STAGE_INPUT": "candidate",
    }.get(stage)
    if candidate_role is not None:
        selected = next(row["artifact"] for row in roles if row["role"] == candidate_role)
        if not isinstance(selected, dict):
            raise AuthorityScenarioError("fixture_candidate_invalid")
        candidate = selected if candidate is None else candidate
        candidate_path = candidate.get("path")
        if not isinstance(candidate_path, str):
            raise AuthorityScenarioError("fixture_candidate_path_invalid")
        paths[candidate_role] = previous.root / candidate_path
        roles = [
            dict(row, artifact=candidate) if row["role"] == candidate_role else row for row in roles
        ]
    return append_private_stage(previous.root, stage, status, candidate, roles, paths, previous)


def registered_capacity_chain(root: Path) -> PrivateAuthority:
    scope = register_private_scope(root)
    rows, paths = replace_private_role(scope, "exact_path_release", "capacity4")
    return append_private_stage(
        root,
        "I1_AUTHORITY_CAPACITY_AMENDED",
        "capacity_amended",
        None,
        rows,
        paths,
        scope,
    )


def registered_i1_accepted_chain(root: Path) -> PrivateAuthority:
    authority = registered_capacity_chain(root)
    for stage in (
        "I1_POSTCOMMIT_CANDIDATE",
        "I1_REVIEW_MATERIAL_FROZEN",
        "I1_EXACT_COMMIT_REVIEW_INPUT",
        "I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED",
        "I1_EXACT_COMMIT_REVIEW_ACCEPTED",
        "I1_FINAL_GATES_ACCEPTED",
        "I1_ACCEPTED",
    ):
        authority = advance_authority(authority, stage)
    return authority


def append_source_cycle(
    previous: PrivateAuthority, *, label: str = "source"
) -> tuple[PrivateAuthority, PrivateAuthority]:
    source_input = advance_authority(previous, "SOURCE_STAGE_INPUT", label=f"{label}-input")
    accepted = advance_authority(
        source_input,
        "SOURCE_STAGE_ACCEPTED",
        label=f"{label}-accepted",
        candidate=json.loads(source_input.record.read_text())["candidate"],
    )
    return source_input, accepted


def late_baseline_chain(root: Path) -> PrivateAuthority:
    scope = register_private_scope(root)
    roles = json.loads(scope.record.read_text())["roles"]
    return append_private_stage(
        root, "I1_AUTHORITY_BASELINE", "baseline", None, roles, scope.role_paths, scope
    )


def invalid_capacity_chain(root: Path, mode: str) -> PrivateAuthority:
    previous = (
        register_private_baseline(root)
        if mode == "wrong_parent"
        else registered_capacity_chain(root)
    )
    if mode == "late_capacity":
        previous = advance_authority(previous, "I1_POSTCOMMIT_CANDIDATE")
    candidate = None
    if mode == "nonnull":
        roles = json.loads(previous.record.read_text())["roles"]
        candidate = roles[0]["artifact"]
    return advance_authority(previous, "I1_AUTHORITY_CAPACITY_AMENDED", candidate=candidate)


def _source(node: str, statement: str) -> Mapping[str, JsonValue]:
    return {
        "sha256": "1" * 64,
        "module_ast_sha256": "2" * 64,
        "nodes": {node: "3" * 64},
        "statements": [{"identity": statement, "sha256": "4" * 64}],
        **dict.fromkeys(GUARDED_FIELDS, []),
    }


def build_structural_case(
    root: Path,
    *,
    lane: str = "I2",
    contract_overrides: Mapping[str, JsonValue] | None = None,
) -> StructuralCase:
    accepted = registered_i1_accepted_chain(root)
    head = fixture_head(accepted)
    baseline = root / f"{CONTROL}/fixture-inputs/structural/baseline.json"
    candidate = root / f"{CONTROL}/fixture-inputs/structural/candidate.json"
    contract = root / f"{CONTROL}/fixture-inputs/structural/contract.json"
    old_node, new_node = "0:FunctionDef:alpha", "0:FunctionDef:alpha"
    old_row, new_row = "module/0:FunctionDef:alpha", "module/0:FunctionDef:alpha"
    facade_node, facade_row = "0:ImportFrom:", "module/0:ImportFrom:"
    baseline_document = {
        "candidate": head,
        "sources": {"backend/old.py": _source(old_node, old_row)},
    }
    candidate_document = {
        "candidate": head,
        "workspace_root": str(root.resolve()),
        "sources": {
            "backend/new.py": _source(new_node, new_row),
            "backend/old.py": _source(facade_node, facade_row),
        },
    }
    baseline_tuple = write_fixture_artifact(
        root, baseline.relative_to(root).as_posix(), baseline_document
    )
    candidate_tuple = write_fixture_artifact(
        root, candidate.relative_to(root).as_posix(), candidate_document
    )
    empty_guard: dict[str, JsonValue] = dict.fromkeys(GUARDED_FIELDS, [])
    contract_document: dict[str, JsonValue] = {
        "schema_version": 1,
        "manifest_kind": "structural_contract",
        "lane_id": lane,
        "candidate": head,
        "baseline": legacy_artifact(root, baseline_tuple),
        "candidate_contract": legacy_artifact(root, candidate_tuple),
        "approved_paths": ["backend/new.py", "backend/old.py"],
        "moves": [{"from": f"backend/old.py::{old_node}", "to": f"backend/new.py::{new_node}"}],
        "ordered_blocks": [
            {
                "from": [f"backend/old.py::{old_row}"],
                "to": [f"backend/new.py::{new_row}"],
            }
        ],
        "facades": {
            f"backend/old.py::{facade_node}": "3" * 64,
            f"backend/old.py::{facade_row}": "4" * 64,
        },
        "expected_guarded": {
            "backend/new.py": empty_guard,
            "backend/old.py": empty_guard,
        },
    }
    contract_document.update(contract_overrides or {})
    contract_tuple = write_fixture_artifact(
        root, contract.relative_to(root).as_posix(), contract_document
    )
    argv_tuple = write_fixture_artifact(
        root,
        f"{CONTROL}/fixture-inputs/structural/compare-argv.json",
        {"manifest_kind": "argv_manifest", "cwd": "backend", "argv": ["compare", lane]},
    )
    artifacts = {"baseline": baseline_tuple, "candidate": candidate_tuple}
    artifacts |= {"structural_contract": contract_tuple, "compare_argv": argv_tuple}
    authority = advance_authority(
        accepted,
        "SOURCE_STAGE_INPUT",
        label="structural",
        candidate=candidate_tuple,
        role_artifacts=artifacts,
    )
    return StructuralCase(authority, baseline, candidate, contract, candidate_tuple)
