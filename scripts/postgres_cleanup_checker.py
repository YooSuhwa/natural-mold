"""Read-only validation for disposable PostgreSQL lifecycle manifests."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Final

from postgres_manifest_io import (
    ManifestPathError,
    read_manifest_bytes,
)

_HEX_64: Final = re.compile(r"[0-9a-f]{64}")
_SECRET_MATERIAL: Final = re.compile(
    r"postgresql(?:\+[^:]*)?://|DATABASE_URL|PASSWORD|authorization|cookie",
    re.IGNORECASE,
)
_DOCKER: Final = shutil.which("docker") or "/usr/bin/docker"
_PS: Final = shutil.which("ps") or "/bin/ps"


class ManifestValidationError(RuntimeError):
    """Stable validation reason without reflecting manifest content."""


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ManifestValidationError(reason)


def _string(value: object, reason: str) -> str:
    if not isinstance(value, str):
        raise ManifestValidationError(reason)
    return value


def _integer(value: object, reason: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ManifestValidationError(reason)
    return value


def _string_list(value: object, reason: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ManifestValidationError(reason)
    return [item for item in value if isinstance(item, str)]


def _mapping(value: object, reason: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ManifestValidationError(reason)
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _mapping_list(value: object, reason: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ManifestValidationError(reason)
    return [_mapping(item, reason) for item in value]


def _validate_test_receipt(receipt: dict[str, object]) -> None:
    selected = _string_list(receipt.get("selected_node_ids"), "selected_nodes")
    executed = _string_list(receipt.get("executed_node_ids"), "executed_nodes")
    _require(selected == executed and len(selected) > 0, "node_execution_mismatch")
    _require(receipt.get("deselected_node_ids") == [], "deselected_nodes")
    _require(receipt.get("failed_node_ids") == [], "failed_nodes")
    _require(receipt.get("skipped_node_ids") == [], "skipped_nodes")
    digest = hashlib.sha256("\n".join(selected).encode()).hexdigest()
    _require(receipt.get("selected_sha256") == digest, "node_hash")
    resources = _mapping(receipt.get("resource_receipt"), "resource_receipt")
    expected = {
        "database_checked_out": 0,
        "checkpointer_pool_published": False,
        "checkpointer_published": False,
        "conversation_tasks_active": 0,
        "skill_worker_task_active": False,
    }
    _require(resources == expected, "resource_receipt")


def _validate_process_identity(scenario: dict[str, object]) -> None:
    _require(_integer(scenario.get("process_id"), "process_id") > 0, "process_id")
    identity = _string(scenario.get("process_identity_sha256"), "process_identity")
    _require(_HEX_64.fullmatch(identity) is not None, "process_identity")


def _validate_container_identity(scenario: dict[str, object], *, required: bool) -> bool:
    has_id = "container_id" in scenario
    has_hash = "container_id_sha256" in scenario
    _require(has_id == has_hash, "container_identity")
    if not has_id:
        _require(not required, "container_identity")
        return False
    container_id = _string(scenario.get("container_id"), "container_identity")
    container_hash = _string(scenario.get("container_id_sha256"), "container_identity")
    _require(_HEX_64.fullmatch(container_id) is not None, "container_id")
    _require(
        container_hash == hashlib.sha256(container_id.encode()).hexdigest(),
        "container_hash",
    )
    return True


def _validate_cleanup_receipt(
    scenario: dict[str, object], *, allow_unobserved_foreign: bool = False
) -> None:
    _require(scenario.get("cleanup_container_removed") is True, "cleanup_container")
    _require(scenario.get("owned_label_absent") is True, "owned_label")
    _require(scenario.get("port_mapping_removed") is True, "port_mapping")
    _require(scenario.get("process_group_stopped") is True, "process_group")
    _require(scenario.get("cleanup_run_root_removed") is True, "cleanup_run_root")
    observed = scenario.get("foreign_containers_observed")
    _require(isinstance(observed, bool), "foreign_container_observation")
    if observed:
        _require(scenario.get("foreign_containers_preserved") is True, "foreign_container")
    else:
        _require(allow_unobserved_foreign, "foreign_container_observation")
        _require(
            scenario.get("foreign_containers_preserved") is None,
            "foreign_container_observation",
        )
    _require(scenario.get("warning_hits") == [], "resource_warning")


def _validate_progressive_observations(
    scenario: dict[str, object], *, container_created: bool, require_complete: bool
) -> None:
    storage_inspected = scenario.get("storage_inspected")
    port_observed = scenario.get("port_mapping_observed")
    _require(isinstance(storage_inspected, bool), "storage_observation")
    _require(isinstance(port_observed, bool), "port_observation")
    if require_complete:
        _require(storage_inspected is True, "storage_observation")
        _require(port_observed is True, "port_observation")
    if not container_created:
        _require(storage_inspected is False and port_observed is False, "container_observation")
    if storage_inspected:
        _require(scenario.get("tmpfs_storage") is True, "tmpfs_storage")
    else:
        _require(scenario.get("tmpfs_storage") is False, "tmpfs_observation")
    if port_observed:
        _require(storage_inspected is True, "port_observation")
        _require(_integer(scenario.get("port"), "port") > 0, "port")
        _require(_integer(scenario.get("port"), "port") <= 65535, "port")
    else:
        _require("port" not in scenario, "port_observation")


def _validate_run_root(scenario: dict[str, object], *, require_created: bool) -> None:
    created = scenario.get("run_root_created")
    _require(isinstance(created, bool), "run_root_observation")
    if require_created:
        _require(created is True, "run_root_observation")
    if created:
        _string(scenario.get("run_root"), "run_root")
    else:
        _require("run_root" not in scenario, "run_root_observation")


def _validate_scenario(scenario: dict[str, object], *, require_test_receipt: bool) -> None:
    _require(scenario.get("status") == "passed", "scenario_status")
    _require(scenario.get("image") == "postgres:16-alpine", "image")
    _require(str(scenario.get("server_version_num", "")).startswith("16"), "server_major")
    _validate_process_identity(scenario)
    _validate_container_identity(scenario, required=True)
    _validate_progressive_observations(scenario, container_created=True, require_complete=True)
    _validate_run_root(scenario, require_created=True)
    _require(scenario.get("alembic_head") == scenario.get("alembic_current"), "alembic_current")
    _require(scenario.get("second_upgrade_idempotent") is True, "alembic_idempotence")
    _validate_cleanup_receipt(scenario)
    _require(scenario.get("warning_scan_complete") is True, "warning_observation")
    receipt = scenario.get("test_receipt")
    child_exit_code = scenario.get("child_exit_code")
    if require_test_receipt:
        _require(_integer(child_exit_code, "child_exit_code") == 0, "child_exit_code")
        _validate_test_receipt(_mapping(receipt, "test_receipt"))
    else:
        _require(receipt is None, "unexpected_test_receipt")


def _validate_interrupted_scenario(scenario: dict[str, object]) -> None:
    _require(scenario.get("status") == "interrupted", "scenario_status")
    _require(scenario.get("failure_reason") == "signal", "interrupt_reason")
    _require(scenario.get("child_exit_code") in {130, 143}, "interrupt_exit")
    _require(scenario.get("image") == "postgres:16-alpine", "image")
    _validate_process_identity(scenario)
    container_created = _validate_container_identity(scenario, required=False)
    _validate_progressive_observations(
        scenario, container_created=container_created, require_complete=False
    )
    _validate_run_root(scenario, require_created=False)
    _validate_cleanup_receipt(scenario, allow_unobserved_foreign=True)
    _require(scenario.get("test_receipt") is None, "unexpected_test_receipt")
    _require(scenario.get("warning_scan_complete") is False, "warning_observation")


def validate_payload(payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, sort_keys=True)
    _require(_SECRET_MATERIAL.search(encoded) is None, "secret_material")
    _require(payload.get("schema_version") == 1, "schema_version")
    scenarios = _mapping_list(payload.get("scenarios"), "scenarios")
    match payload.get("mode"):
        case (
            "all" | "migration-roundtrip" | "stream-resume" | "run-lifecycle+stream-resume"
        ) as mode:
            _require(payload.get("concurrent_pair") is False, "concurrent_pair")
            match mode:
                case "all":
                    scenario_reason = "all_scenarios"
                case "migration-roundtrip":
                    scenario_reason = "migration_roundtrip_scenarios"
                case "stream-resume":
                    scenario_reason = "stream_resume_scenarios"
                case "run-lifecycle+stream-resume":
                    scenario_reason = "run_lifecycle_stream_resume_scenarios"
            _require(
                len(scenarios) == 1 and scenarios[0].get("scenario") == mode,
                scenario_reason,
            )
            match payload.get("status"):
                case "passed":
                    _validate_scenario(
                        scenarios[0], require_test_receipt=mode != "migration-roundtrip"
                    )
                    if mode == "migration-roundtrip":
                        _require(
                            scenarios[0].get("child_exit_code") == 0,
                            "child_exit_code",
                        )
                        _require(
                            scenarios[0].get("migration_roundtrip") is True,
                            "migration_roundtrip",
                        )
                case "interrupted":
                    _validate_interrupted_scenario(scenarios[0])
                case _:
                    raise ManifestValidationError("manifest_status")
        case "self-test":
            _require(payload.get("concurrent_pair") is False, "concurrent_pair")
            expected = [("success", 0), ("child_failure", 23), ("sigint", 130)]
            status = payload.get("status")
            _require(status in {"passed", "interrupted"}, "manifest_status")
            interrupted = status == "interrupted"
            expected_count = range(1, len(expected) + 1) if interrupted else {len(expected)}
            _require(len(scenarios) in expected_count, "self_test_outcomes")
            for index, scenario in enumerate(scenarios):
                expected_name, expected_code = expected[index]
                _require(scenario.get("scenario") == expected_name, "self_test_outcomes")
                if interrupted and index == len(scenarios) - 1:
                    _validate_interrupted_scenario(scenario)
                else:
                    _require(scenario.get("child_exit_code") == expected_code, "self_test_outcomes")
                    _validate_scenario(scenario, require_test_receipt=False)
        case _:
            raise ManifestValidationError("mode")


def validate_live_absence(payload: dict[str, object]) -> None:
    scenarios = _mapping_list(payload.get("scenarios"), "scenarios")
    for scenario in scenarios:
        if "container_id" in scenario:
            container_id = _string(scenario.get("container_id"), "container_id")
            inspect = subprocess.run(  # noqa: S603 - parsed immutable container ID
                [_DOCKER, "inspect", container_id],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            _require(inspect.returncode != 0, "live_container")
        if scenario.get("run_root_created") is True:
            _require(
                not Path(_string(scenario.get("run_root"), "run_root")).exists(),
                "live_run_root",
            )
        process_id = _integer(scenario.get("process_id"), "process_id")
        process = subprocess.run(  # noqa: S603 - parsed integer PID
            [_PS, "-p", str(process_id), "-o", "lstart=,command="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if process.returncode == 0 and process.stdout:
            live_hash = hashlib.sha256(process.stdout.encode()).hexdigest()
            expected_hash = _string(scenario.get("process_identity_sha256"), "process_identity")
            _require(live_hash != expected_hash, "live_process")


def load_manifest(path: Path) -> dict[str, object]:
    try:
        raw = read_manifest_bytes(path)
    except ManifestPathError as error:
        raise ManifestValidationError("manifest_file") from error
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ManifestValidationError("manifest_json") from error
    if not isinstance(payload, dict):
        raise ManifestValidationError("manifest_shape")
    return _mapping(payload, "manifest_shape")


def load_and_validate(path: Path) -> None:
    payload = load_manifest(path)
    validate_payload(payload)
    validate_live_absence(payload)
