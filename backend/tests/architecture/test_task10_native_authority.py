from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts import task10_native_evidence as native
from scripts.task10_root_authority import resolve_role, validate_authority
from scripts.task10_root_authority_validation import AuthorityValidationError
from tests.architecture.task10_authority_fixtures import (
    CONTROL,
    JsonValue,
    fixture_artifact,
    legacy_artifact,
    register_private_scope,
)
from tests.architecture.task10_authority_literal_catalog import (
    I24A,
    NA,
    NI,
    NP,
    STAGES,
    R,
)
from tests.architecture.task10_authority_scenarios import (
    advance_authority,
    append_source_cycle,
    registered_i1_accepted_chain,
)

COLLECT_ARGV = [".venv/bin/pytest", "--collect-only", "-q", "-p"]
COLLECT_ARGV += ["tests.postgres_execution_plugin", "tests", "-m", "integration"]
SELECTION = "native_selection_not_preregistered"
COLLECT = "native_collect_execution_nonempty"
RESOURCE = "native_resource_cleanup_incomplete"


@dataclass(frozen=True, slots=True)
class NativeCase:
    paths: dict[str, Path]
    artifacts: dict[str, dict[str, JsonValue]]


def _digest(rows: list[str]) -> str:
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def _case(root: Path) -> NativeCase:
    paths: dict[str, Path] = {}
    artifacts: dict[str, dict[str, JsonValue]] = {}

    def artifact(
        name: str, value: JsonValue, content_type: str = "metadata"
    ) -> dict[str, JsonValue]:
        path = root / CONTROL / f"fixture-inputs/native/{name}.json"
        paths[name] = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
        registered = fixture_artifact(root, path, content_type)
        artifacts[name] = registered
        return legacy_artifact(root, registered)

    artifact("static_registry", {"fixture": "full-static-registry"})
    registry = [f"backend/tests/integration/test_task_{index}.py" for index in range(1, 7)]
    witness_modules = registry[:5]
    witnesses = [f"{path}::test_required" for path in witness_modules]
    selected = [*witnesses, f"{registry[-1]}::test_existing"]
    intent = {
        "schema_version": 1,
        "kind": "task10-native-intent",
        "candidate": "a" * 40,
        "canonical_all_selector": {"cwd": "backend", "argv": COLLECT_ARGV},
        "static_registry": legacy_artifact(root, artifacts["static_registry"]),
        "registry_paths": registry,
        "registry_sha256": _digest(registry),
        "task23_witness_modules": witness_modules,
        "required_witnesses": witnesses,
        "status": "canonical_all_registry_static_uncollected",
    }
    plugin_relative = "scratch/native-collection.json"
    command = {
        "manifest_kind": "native_command_manifest",
        "command": "prepare-native-collect-only",
        "cwd": "backend",
        "argv": COLLECT_ARGV,
        "env_overrides": {"MOLDY_PG_TEST_RECEIPT": str((root / plugin_relative).resolve())},
    }
    progress = {
        "schema_version": 1,
        "sequence": 3,
        "state": "finished",
        "session_elapsed_ns": 7,
        "active_node_id": None,
        "active_phase": "session_finish",
        "completed_node_ids": [],
        "node_elapsed_ns": {},
    }
    receipt = {
        "schema_version": 1,
        "selected_node_ids": selected,
        "executed_node_ids": [],
        "deselected_node_ids": [],
        "failed_node_ids": [],
        "skipped_node_ids": [],
        "selected_sha256": _digest(selected),
        "resource_receipt": native.EXPECTED_RESOURCES,
        "progress": progress,
    }
    preparation = {
        "schema_version": 1,
        "kind": "task10-native-preparation",
        "candidate": intent["candidate"],
        "mode": "canonical_all_collect_only",
        "plugin_receipt": artifact("native_collection_receipt", receipt),
        "selected_node_ids": selected,
        "selected_sha256": _digest(selected),
        "required_witnesses": witnesses,
    }
    artifact("native_intent", intent)
    artifact("native_preparation_command", command)
    artifact("native_preparation_evidence", preparation)
    executed = receipt | {
        "executed_node_ids": selected,
        "progress": progress
        | {"completed_node_ids": selected, "node_elapsed_ns": dict.fromkeys(selected, 1)},
    }
    scenario: dict[str, JsonValue] = dict.fromkeys(native.REQUIRED_TRUE.split(), True)
    scenario |= {
        "scenario": "all",
        "status": "passed",
        "child_exit_code": 0,
        "process_id": 42,
        "process_identity_sha256": "1" * 64,
        "container_id": "container",
        "container_id_sha256": hashlib.sha256(b"container").hexdigest(),
        "image": "postgres:16-alpine",
        "server_version_num": "160015",
        "alembic_head": "head",
        "alembic_current": "head",
        "warning_hits": [],
        "test_receipt": executed,
    }
    run = {"schema_version": 1, "status": "passed", "mode": "all"}
    run |= {"concurrent_pair": False, "scenarios": [scenario]}
    artifact("native_execution_evidence", run)
    source = {"candidate": intent["candidate"], "required_witnesses": witnesses}
    source |= {"selected_node_ids": selected, "selected_sha256": _digest(selected)}
    artifact("native_source_contract", source)
    bound = {R, "PARENT_REVIEW_MATERIAL_FROZEN", "I24_REVIEW_INPUT"}
    bound |= {"I24_REVIEW_RAW_RECEIVED", I24A, NP, NI, NA}
    for stage, _, definitions in STAGES:
        if stage in bound:
            for definition in definitions:
                if definition.name not in artifacts:
                    value = {"fixture": definition.name, "manifest_kind": definition.manifest_kind}
                    artifact(definition.name, value, definition.content_type)
    return NativeCase(paths, artifacts)


def _prepare(case: NativeCase) -> int:
    prepared = json.loads(case.paths["native_preparation_evidence"].read_text())
    return native.collect_only_selected(
        case.paths["native_collection_receipt"],
        prepared["selected_node_ids"],
        prepared["selected_sha256"],
        prepared["required_witnesses"],
    )


def _mutate(case: NativeCase, name: str, key: str, value: JsonValue) -> None:
    document = json.loads(case.paths[name].read_text())
    parent, separator, child = key.partition(".")
    target = document[parent] if separator else document
    target[child if separator else parent] = value
    case.paths[name].write_text(json.dumps(document, sort_keys=True, separators=(",", ":")))


def _roles(case: NativeCase, stage: str) -> dict[str, dict[str, JsonValue]]:
    definitions = next(roles for name, _, roles in STAGES if name == stage)
    return {definition.name: case.artifacts[definition.name] for definition in definitions}


def test_capacity_full_catalog_witness_subset_collect_only_specialized(tmp_path: Path) -> None:
    authority = registered_i1_accepted_chain(tmp_path / "authority-root")
    _, authority = append_source_cycle(authority, label="native")
    case = _case(authority.root)
    assert _prepare(case) == 6
    intent = json.loads(case.paths["native_intent"].read_text())
    command = json.loads(case.paths["native_preparation_command"].read_text())
    assert set(intent["task23_witness_modules"]) < set(intent["registry_paths"])
    assert command["argv"] == COLLECT_ARGV
    stages = [R, "PARENT_REVIEW_MATERIAL_FROZEN", "I24_REVIEW_INPUT"]
    stages += ["I24_REVIEW_RAW_RECEIVED", I24A, NP, NI, NA]
    for stage in stages:
        authority = advance_authority(authority, stage, role_artifacts=_roles(case, stage))
    chain = validate_authority(authority.root, authority.seal, require_stage=NA)
    assert resolve_role(chain, R, "native_intent") == resolve_role(chain, NP, "native_intent")
    assert resolve_role(chain, R, "native_preparation_command") == resolve_role(
        chain, NP, "native_preparation_command"
    )
    source = json.loads(case.paths["native_source_contract"].read_text())
    assert native.canonical_selected(case.paths["native_execution_evidence"], source) == 6


@pytest.mark.parametrize(
    ("name", "key", "value", "code"),
    [
        ("native_preparation_evidence", "selected_sha256", "0" * 64, SELECTION),
        ("native_preparation_evidence", "required_witnesses", [], "native_witness_missing"),
        ("native_collection_receipt", "schema_version", True, "native_collection_schema_invalid"),
        ("native_collection_receipt", "selected_node_ids", ["duplicate", "duplicate"], SELECTION),
        ("native_collection_receipt", "selected_sha256", "0" * 64, SELECTION),
        ("native_collection_receipt", "executed_node_ids", ["unexpected"], COLLECT),
        ("native_collection_receipt", "deselected_node_ids", ["unexpected"], COLLECT),
        ("native_collection_receipt", "failed_node_ids", ["unexpected"], COLLECT),
        ("native_collection_receipt", "skipped_node_ids", ["unexpected"], COLLECT),
        ("native_collection_receipt", "progress.sequence", True, "native_progress_incomplete"),
        (
            "native_collection_receipt",
            "progress.session_elapsed_ns",
            1.0,
            "native_progress_incomplete",
        ),
        ("native_collection_receipt", "progress.unknown", 0, "native_progress_incomplete"),
        ("native_collection_receipt", "resource_receipt", {}, RESOURCE),
        (
            "native_collection_receipt",
            "resource_receipt",
            dict.fromkeys(native.EXPECTED_RESOURCES, False),
            RESOURCE,
        ),
    ],
)
def test_collect_receipt_failures_skips_desel_resources_wrong_types_reject(
    tmp_path: Path, name: str, key: str, value: JsonValue, code: str
) -> None:
    case = _case(tmp_path)
    _mutate(case, name, key, value)
    with pytest.raises(native.NativeEvidenceError, match=code):
        _prepare(case)


def test_canonical_bool_exit_and_completed_order_reject(tmp_path: Path) -> None:
    case = _case(tmp_path)
    source = json.loads(case.paths["native_source_contract"].read_text())
    manifest = json.loads(case.paths["native_execution_evidence"].read_text())
    scenario = manifest["scenarios"][0]
    scenario["child_exit_code"] = False
    case.paths["native_execution_evidence"].write_text(json.dumps(manifest))
    with pytest.raises(native.NativeEvidenceError, match="native_suite_incomplete"):
        native.canonical_selected(case.paths["native_execution_evidence"], source)
    scenario["child_exit_code"] = 0
    scenario["test_receipt"]["progress"]["completed_node_ids"].reverse()
    case.paths["native_execution_evidence"].write_text(json.dumps(manifest))
    with pytest.raises(native.NativeEvidenceError, match="native_test_sets_mismatch"):
        native.canonical_selected(case.paths["native_execution_evidence"], source)


def test_scope3_fallback_rejects_before_native_preparation(tmp_path: Path) -> None:
    scope = register_private_scope(tmp_path / "scope-only")
    with pytest.raises(AuthorityValidationError):
        validate_authority(scope.root, scope.seal, require_stage=NP)
