"""Root-authorized Task 10 review facade and legacy compatibility utilities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from scripts import task10_review_metadata as metadata
from scripts import task10_review_report as reports
from scripts import task10_root_artifact_io as artifact_io
from scripts import task10_root_authority as authority
from scripts import task10_root_authority_contract as contract

type JsonValue = artifact_io.JsonValue
type JsonObject = dict[str, JsonValue]

RELEASE_SHA: Final = "71bf7c386701ad914835a45cc8a7bfe857026c4b23a03b7c9d48b15a9c6406a1"
PLAN_SHA: Final = "1405651ac3f5bac2cbb7b4ae71a34484d6e322f49535fe899e9e6d2396bb9eb6"
TASK7_REVIEW_SHA: Final = "c457b18833e0f46c1563f7127b4eb3054e403bfb3adb71c98e0c4b458e233422"
R7_BINDING_SHA: Final = "2aa00bbec9d87bb0d44dab996812b74beedd6255e49f34f2b9d467cce1f26015"
AMENDMENT_SHA: Final = "cea59e319f989f0a03a752f1465dfb5f93398ad7ee9b00706f44ecb9785a0c29"
PARSER_AMENDMENT_SHA: Final = "f8360cb1a162a4f6feee9e50d3e418a3082ceea9188ae85198d76fdf7f895837"
NATIVE_AMENDMENT_SHA: Final = "b9437fa2358f578ba26f8333033842a6edddaa27f269b3091083cf15bceb6515"
STAGE_AMENDMENT_SHA: Final = "1cd9dd1864ca00db2b375cf59cc0dd4fad35173e96153c21792116c94fcf7ede"
I1_RAW: Final = "I1_EXACT_COMMIT_REVIEW_RAW_RECEIVED"
ROLE_NAMES: Final = ("plan", "candidate", "evidence", "native_source", "cleanup")


BindingError = metadata.ReviewMetadataError


@dataclass(frozen=True, slots=True)
class ReviewCommand:
    inputs: tuple[Path, ...]
    outputs: tuple[Path, ...]


def _require(condition: bool, error: str) -> None:
    if not condition:
        raise BindingError(error)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> JsonObject:
    value: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BindingError("json_object_required")
    return value


def write(path: Path, value: Mapping[str, JsonValue]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def file_tuple(path: Path) -> JsonObject:
    if not path.is_file():
        raise BindingError(f"bound_file_missing:{path}")
    return {"path": str(path.resolve()), "sha256": sha256(path), "bytes": path.stat().st_size}


def verify_tuple(value: JsonValue, code: str) -> Path:
    if not isinstance(value, dict) or set(value) != {"path", "sha256", "bytes"}:
        raise BindingError(code)
    raw, digest, size = value.get("path"), value.get("sha256"), value.get("bytes")
    if not isinstance(raw, str) or not isinstance(digest, str) or type(size) is not int:
        raise BindingError(code)
    path = Path(raw)
    valid = len(digest) == 64 and path.is_file() and sha256(path) == digest
    _require(valid and path.stat().st_size == size, code)
    return path.resolve()


def validate_binding(binding: Path, seal: Path) -> JsonObject:
    document, sealed = load(binding), load(seal)
    roles = document.get("roles")
    if not isinstance(roles, dict) or set(roles) != set(ROLE_NAMES):
        raise BindingError("review_roles_invalid")
    for role, value in roles.items():
        verify_tuple(value, f"review_role_changed:{role}")
    review_input = document.get("review_input")
    verify_tuple(review_input, "review_input_changed")
    metadata.validate_review_binding(
        document, str(document.get("candidate")), roles, _object(review_input)
    )
    metadata.validate_binding_seal(
        sealed,
        str(document.get("candidate")),
        file_tuple(binding),
        _object(sealed.get("review_input")),
        _object(sealed.get("freeze_argv")),
    )
    return document


def _object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise BindingError("review_document_schema")
    return value


def git_state(root: Path) -> tuple[str, list[str]]:
    state = authority.capture_git_state(root)
    head, paths = state.get("head"), state.get("canonical_paths")
    if not isinstance(head, str) or not isinstance(paths, list):
        raise BindingError("git_state_failed")
    return head, [item for item in paths if isinstance(item, str)]


def _document(
    chain: authority.AuthorityChain, artifact: artifact_io.AuthorityArtifact
) -> JsonObject:
    try:
        value: JsonValue = json.loads(artifact_io.read_regular(chain.root, artifact["path"]))
    except (OSError, json.JSONDecodeError) as exc:
        raise BindingError("review_document_invalid") from exc
    return _object(value)


def _legacy(chain: authority.AuthorityChain, artifact: artifact_io.AuthorityArtifact) -> JsonObject:
    return {
        "path": str((chain.root / artifact["path"]).resolve()),
        "sha256": artifact["sha256"],
        "bytes": artifact["bytes"],
    }


def _role(chain: authority.AuthorityChain, stage: str, role: str) -> JsonObject:
    return _legacy(chain, authority.resolve_role(chain, stage, role))


def _context(root: Path, seal: Path, stages: set[str]) -> authority.AuthorityChain:
    chain = authority.validate_authority(root, seal)
    _require(chain.records[-1].get("stage") in stages, "review_stage_unsupported")
    _require(
        authority.resolve_active_scope(chain).get("stage") == "I1_AUTHORITY_CAPACITY_AMENDED",
        "active_scope_not_capacity_four",
    )
    authority.validate_live_git(chain)
    return chain


def _command(
    chain: authority.AuthorityChain, name: str, request: ReviewCommand
) -> tuple[artifact_io.AuthorityArtifact, ...]:
    stage = chain.records[-1].get("stage")
    if not isinstance(stage, str) or (name, stage) not in contract.COMMAND_SPECS:
        raise BindingError("review_command_stage")
    spec = contract.COMMAND_SPECS[(name, stage)]
    _require(len(request.inputs) == len(spec.inputs), "review_argument_count")
    argv = _document(chain, authority.resolve_role(chain, spec.argv.stage, spec.argv.role))
    metadata.validate_registered_argv(argv)
    artifacts = tuple(authority.resolve_role(chain, item.stage, item.role) for item in spec.inputs)
    expected = tuple((chain.root / item["path"]).resolve() for item in artifacts)
    _require(
        tuple(path.resolve() for path in request.inputs) == expected,
        "authority_argument_path_mismatch",
    )
    return artifacts


def _publish(chain: authority.AuthorityChain, path: Path, value: Mapping[str, JsonValue]) -> None:
    try:
        relative = path.resolve().relative_to(chain.root).as_posix()
    except ValueError as exc:
        raise BindingError("output_outside_root") from exc
    artifact_io.write_public_output(chain.root, relative, artifact_io.canonical_json_bytes(value))


def _candidate(chain: authority.AuthorityChain, artifact: artifact_io.AuthorityArtifact) -> str:
    head = chain.records[-1].get("git_state")
    expected = head.get("head") if isinstance(head, dict) else None
    if not isinstance(expected, str):
        raise BindingError("candidate_manifest_invalid")
    metadata.validate_candidate_document(_document(chain, artifact), expected, str(chain.root))
    return expected


def freeze_review(root: Path, authority_seal: Path, request: ReviewCommand) -> None:
    chain = _context(root, authority_seal, {"I1_POSTCOMMIT_CANDIDATE", "I23_FREEZE_READY"})
    artifacts = _command(chain, "freeze-review", request)
    _require(len(request.outputs) == 2, "review_output_count")
    candidate = _candidate(chain, artifacts[1])
    roles = {name: _legacy(chain, item) for name, item in zip(ROLE_NAMES, artifacts, strict=True)}
    review = reports.review_input_document(
        candidate, roles, reports.grammar_from_registered_stage(str(chain.records[-1]["stage"]))
    )
    _publish(chain, request.outputs[0], review)
    review_tuple = file_tuple(request.outputs[0])
    binding = metadata.build_review_binding(candidate, roles, review_tuple)
    _publish(chain, request.outputs[1], binding)


def _binding(
    chain: authority.AuthorityChain, artifact: artifact_io.AuthorityArtifact, material: str
) -> JsonObject:
    origin = "I1_POSTCOMMIT_CANDIDATE" if material == contract.I1_MATERIAL else "I23_FREEZE_READY"
    inputs = contract.COMMAND_SPECS[("freeze-review", origin)].inputs
    roles = {
        name: _legacy(chain, authority.resolve_role(chain, item.stage, item.role))
        for name, item in zip(ROLE_NAMES, inputs, strict=True)
    }
    review_input = _role(chain, material, "review_input")
    candidate = _candidate(chain, authority.resolve_role(chain, inputs[1].stage, inputs[1].role))
    return metadata.validate_review_binding(
        _document(chain, artifact), candidate, roles, review_input
    )


def seal_review_binding(root: Path, authority_seal: Path, request: ReviewCommand) -> None:
    chain = _context(root, authority_seal, {contract.I1_MATERIAL, contract.PARENT_MATERIAL})
    artifacts = _command(chain, "seal-review-binding", request)
    _require(len(request.outputs) == 1, "review_output_count")
    binding = _binding(chain, artifacts[1], str(chain.records[-1]["stage"]))
    value = metadata.build_binding_seal(
        str(binding["candidate"]),
        _legacy(chain, artifacts[1]),
        _legacy(chain, artifacts[0]),
        _legacy(chain, artifacts[2]),
    )
    _publish(chain, request.outputs[0], value)


def _route(material: str) -> tuple[tuple[str, str], tuple[str, str]]:
    if material == contract.I1_MATERIAL:
        return ("I1_AUTHORITY_CAPACITY_AMENDED", "r7_plan"), (
            "I1_POSTCOMMIT_CANDIDATE",
            "candidate_manifest",
        )
    return ("I23_FREEZE_READY", "plan"), ("I23_FREEZE_READY", "final_candidate")


def _facts(chain: authority.AuthorityChain, material: str) -> reports.ReviewHashFacts:
    plan, candidate = _route(material)
    return reports.ReviewHashFacts(
        authority.resolve_role(chain, *plan)["sha256"],
        authority.resolve_role(chain, *candidate)["sha256"],
        authority.resolve_role(chain, material, "review_binding")["sha256"],
    )


def _metadata(
    chain: authority.AuthorityChain, material: str, input_stage: str, raw_stage: str
) -> None:
    request = _document(chain, authority.resolve_role(chain, input_stage, "reviewer_request"))
    provenance = _document(chain, authority.resolve_role(chain, raw_stage, "reviewer_provenance"))
    binding = authority.resolve_role(chain, material, "review_binding")
    plan, _ = _route(material)
    text_role = authority.resolve_role(chain, input_stage, "review_request_text")
    text = artifact_io.read_regular(chain.root, text_role["path"]).decode()
    expected_text = reports.reviewer_instruction(
        reports.grammar_from_registered_stage(input_stage), _facts(chain, material)
    )
    task = request.get("task_name")
    launch_role = authority.resolve_role(chain, raw_stage, "review_launch_receipt")
    launch = _document(chain, launch_role)
    expected_request: JsonObject = {
        "task_name": task,
        "message_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "request_text": _legacy(chain, text_role),
        "plan": _role(chain, *plan),
        "review_input": _role(chain, material, "review_input"),
        "binding": _legacy(chain, binding),
        "binding_seal": _role(chain, input_stage, "review_binding_seal"),
        "binding_sha256": binding["sha256"],
        "candidate": _binding(chain, binding, material)["candidate"],
    }
    raw = authority.resolve_role(chain, raw_stage, "review_raw")
    expected_provenance: JsonObject = {
        "request": _role(chain, input_stage, "reviewer_request"),
        "agent_id": launch.get("agent_id"),
        "session_id": launch.get("session_id"),
        "launch_receipt": _legacy(chain, launch_role),
        "binding_sha256": binding["sha256"],
        "candidate": expected_request["candidate"],
        "raw_report": _legacy(chain, raw),
        "raw_sha256": raw["sha256"],
    }
    metadata.validate_reviewer_exchange(
        request,
        provenance,
        expected_request,
        expected_provenance,
        text,
        expected_text,
        raw["sha256"],
    )


def verify_review(root: Path, authority_seal: Path, request: ReviewCommand) -> None:
    chain = _context(root, authority_seal, {I1_RAW, "I24_REVIEW_RAW_RECEIVED"})
    raw_stage = str(chain.records[-1]["stage"])
    name = "verify-review-i1" if raw_stage == I1_RAW else "verify-review-i24"
    artifacts = _command(chain, name, request)
    _require(len(request.outputs) == 1, "review_output_count")
    material = contract.I1_MATERIAL if raw_stage == I1_RAW else contract.PARENT_MATERIAL
    input_stage = contract.I1_INPUT if raw_stage == I1_RAW else "I24_REVIEW_INPUT"
    binding = _binding(chain, artifacts[0], material)
    seal = _document(chain, artifacts[1])
    metadata.validate_binding_seal(
        seal,
        str(binding["candidate"]),
        _legacy(chain, artifacts[0]),
        _role(chain, material, "review_input"),
        _role(chain, material, "seal_argv"),
    )
    _metadata(chain, material, input_stage, raw_stage)
    parsed = reports.parse_review_report(
        request.inputs[2], _facts(chain, material), reports.grammar_from_registered_stage(raw_stage)
    )
    acceptance = metadata.build_review_acceptance(
        str(binding["candidate"]),
        parsed.verdict,
        _legacy(chain, artifacts[0]),
        _legacy(chain, artifacts[1]),
        _legacy(chain, artifacts[2]),
    )
    _publish(chain, request.outputs[0], acceptance)


def verify_release(root: Path, path: Path) -> tuple[JsonObject, list[str]]:
    _require(sha256(path) == RELEASE_SHA, "release_not_frozen")
    release = load(path)
    files = release.get("files")
    plan = root / str(release.get("plan"))
    valid = release.get("plan_sha256") == PLAN_SHA and plan.is_file() and sha256(plan) == PLAN_SHA
    if not valid or not isinstance(files, list):
        raise BindingError("plan_changed")
    paths = [row.get("path") for row in files if isinstance(row, dict)]
    _require(
        len(paths) == 6 and all(isinstance(item, str) for item in paths), "release_file_set_invalid"
    )
    return release, [item for item in paths if isinstance(item, str)]


def verify_owned53(root: Path) -> JsonObject:
    relative = ".omo/evidence/company-administration/wave2-native-expected-owned53.json"
    path = root / relative
    expected = load(path).get("expected_owned_hashes")
    _require(isinstance(expected, dict) and len(expected) == 53, "owned53_manifest_changed")
    return {"path": relative, "sha256": sha256(path), "count": 53, "kind": "baseline_history"}
