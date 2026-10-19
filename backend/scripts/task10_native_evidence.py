"""Validate Task 14 receipts and their separately registered source evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path

if __package__:
    from scripts.task10_review_binding import JsonObject, JsonValue, file_tuple, load, verify_tuple
else:
    from task10_review_binding import JsonObject, JsonValue, file_tuple, load, verify_tuple

EXPECTED_RESOURCES: JsonObject = {
    "database_checked_out": 0,
    "checkpointer_pool_published": False,
    "checkpointer_published": False,
    "conversation_tasks_active": 0,
    "skill_worker_task_active": False,
}
REQUIRED_TRUE = (
    "cleanup_container_removed cleanup_run_root_removed foreign_containers_observed "
    "foreign_containers_preserved owned_label_absent port_mapping_observed "
    "port_mapping_removed process_group_stopped run_root_created second_upgrade_idempotent "
    "storage_inspected tmpfs_storage warning_scan_complete"
)
RECEIPT_KEYS = "schema_version selected_node_ids executed_node_ids deselected_node_ids "
RECEIPT_KEYS += "failed_node_ids skipped_node_ids selected_sha256 resource_receipt progress"


class NativeEvidenceError(ValueError): ...


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise NativeEvidenceError(code)


def _strings(value: JsonValue, code: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise NativeEvidenceError(code)
    return [item for item in value if isinstance(item, str)]


def _digest(value: JsonValue, code: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - set("0123456789abcdef"):
        raise NativeEvidenceError(code)
    return value


def _exact(value: JsonValue, expected: JsonValue) -> bool:
    return type(value) is type(expected) and value == expected


def _resources(value: JsonValue) -> None:
    if not isinstance(value, dict):
        raise NativeEvidenceError("native_resource_cleanup_incomplete")
    keys = set(value) == set(EXPECTED_RESOURCES)
    values = all(_exact(value.get(name), expected) for name, expected in EXPECTED_RESOURCES.items())
    _require(keys and values, "native_resource_cleanup_incomplete")


def _receipt(receipt: JsonObject) -> None:
    _require(set(receipt) == set(RECEIPT_KEYS.split()), "native_collection_schema_invalid")
    _require(_exact(receipt.get("schema_version"), 1), "native_collection_schema_invalid")


def _progress(value: JsonValue) -> list[str]:
    error = "native_progress_incomplete"
    keys = "schema_version sequence state session_elapsed_ns active_node_id active_phase "
    keys += "completed_node_ids node_elapsed_ns"
    if not isinstance(value, dict):
        raise NativeEvidenceError(error)
    schema = set(value) == set(keys.split()) and _exact(value.get("schema_version"), 1)
    _require(schema, error)
    finished = value.get("state") == "finished" and value.get("active_node_id") is None
    _require(finished, error)
    numbers = (value.get("sequence"), value.get("session_elapsed_ns"))
    _require(all(type(item) is int and item >= 0 for item in numbers), error)
    _require(value.get("active_phase") == "session_finish", error)
    completed = _strings(value.get("completed_node_ids"), "native_completed_invalid")
    elapsed = value.get("node_elapsed_ns")
    if not isinstance(elapsed, dict):
        raise NativeEvidenceError(error)
    _require(set(elapsed) == set(completed), error)
    elapsed_valid = all(type(item) is int and item >= 0 for item in elapsed.values())
    _require(elapsed_valid, error)
    return completed


def _selection(
    receipt: JsonObject, nodes: list[str], sha: str, witnesses: list[str], collect: bool
) -> int:
    _receipt(receipt)
    selected = _strings(receipt.get("selected_node_ids"), "native_selected_invalid")
    executed = _strings(receipt.get("executed_node_ids"), "native_executed_invalid")
    completed = _progress(receipt.get("progress"))
    actual = hashlib.sha256("\n".join(selected).encode()).hexdigest()
    ordered = bool(selected) and selected == sorted(set(selected))
    _require(ordered, "native_selection_not_preregistered")
    registered = selected == nodes and receipt.get("selected_sha256") == actual == sha
    _require(registered, "native_selection_not_preregistered")
    expected = [] if collect else selected
    mismatch = "native_collect_execution_nonempty" if collect else "native_test_sets_mismatch"
    _require(executed == completed == expected, mismatch)
    for name in ("deselected", "failed", "skipped"):
        empty = not _strings(receipt.get(f"{name}_node_ids"), f"native_{name}_invalid")
        code = "native_collect_execution_nonempty" if collect else f"native_{name}_nonempty"
        _require(empty, code)
    _resources(receipt.get("resource_receipt"))
    _require(bool(witnesses) and set(witnesses) <= set(selected), "native_witness_missing")
    return len(selected)


def collect_only_selected(path: Path, nodes: list[str], sha: str, witnesses: list[str]) -> int:
    """Validate the final static-selection receipt from collect-only preparation."""
    return _selection(load(path), nodes, sha, witnesses, True)


def _cleanup_entry(index_path: Path, role: str) -> JsonObject:
    entries = load(index_path).get("entries")
    if not isinstance(entries, list):
        raise NativeEvidenceError("cleanup_entries_required")
    matches = [row for row in entries if isinstance(row, dict) and row.get("role") == role]
    _require(len(matches) == 1, f"cleanup_role_invalid:{role}")
    return matches[0]


def verify_cleanup(index_path: Path, role: str, outer_log: Path | None = None) -> JsonObject:
    row = _cleanup_entry(index_path, role)
    receipt = load(verify_tuple(row.get("receipt"), "cleanup_receipt_changed"))
    expected = {"schema_version", "status", "child_exit_code", "cleanup", "run_root_sha256"}
    _require(set(receipt) == expected, "cleanup_receipt_schema_invalid")
    _require(_exact(receipt.get("schema_version"), 1), "cleanup_receipt_schema_invalid")
    _require(receipt.get("status") == "passed", "cleanup_required_stage_failed")
    _require(_exact(receipt.get("child_exit_code"), 0), "cleanup_required_stage_failed")
    _require(receipt.get("cleanup") == "removed", "cleanup_not_removed")
    _digest(receipt.get("run_root_sha256"), "cleanup_run_root_invalid")
    if outer_log is not None:
        actual = verify_tuple(row.get("outer_log"), "native_outer_log_changed")
        _require(actual == outer_log.resolve(), "native_outer_log_path_mismatch")
    return row


def _source_sidecars(source: JsonObject, cleanup: JsonObject) -> dict[str, JsonObject]:
    result: dict[str, JsonObject] = {}
    for name in ("source_before", "source_after", "source_union", "whole_union"):
        source_tuple = source.get(name)
        _require(cleanup.get(name) == source_tuple, f"native_source_cleanup_mismatch:{name}")
        path = verify_tuple(source_tuple, f"native_source_changed:{name}")
        _require(bool(load(path)), f"native_source_empty:{name}")
        result[name] = file_tuple(path)
    return result


def _identity(scenario: JsonObject) -> None:
    process_id, process_hash = scenario.get("process_id"), scenario.get("process_identity_sha256")
    container_id, container_hash = scenario.get("container_id"), scenario.get("container_id_sha256")
    _require(type(process_id) is int and process_id > 0, "native_process_identity_invalid")
    _digest(process_hash, "native_process_identity_invalid")
    if not isinstance(container_id, str):
        raise NativeEvidenceError("native_container_invalid")
    expected = hashlib.sha256(container_id.encode()).hexdigest()
    actual = _digest(container_hash, "native_container_invalid")
    _require(actual == expected, "native_container_invalid")


def _scenario(receipt: JsonObject, witnesses: list[str], registered: list[str], digest: str) -> int:
    return _selection(receipt, registered, digest, witnesses, False)


def canonical_selected(path: Path, source: JsonObject) -> int:
    manifest = load(path)
    scenarios = manifest.get("scenarios")
    invalid = (
        not _exact(manifest.get("schema_version"), 1)
        or manifest.get("status") != "passed"
        or manifest.get("mode") != "all"
        or manifest.get("concurrent_pair") is not False
    )
    if invalid:
        raise NativeEvidenceError("native_suite_incomplete")
    if not isinstance(scenarios, list) or len(scenarios) != 1:
        raise NativeEvidenceError("native_suite_incomplete")
    if not isinstance(scenarios[0], dict):
        raise NativeEvidenceError("native_suite_incomplete")
    scenario = scenarios[0]
    passed = scenario.get("scenario") == "all" and scenario.get("status") == "passed"
    _require(passed and _exact(scenario.get("child_exit_code"), 0), "native_suite_incomplete")
    _identity(scenario)
    required = all(scenario.get(name) is True for name in REQUIRED_TRUE.split())
    _require(required, "native_resource_cleanup_incomplete")
    if (
        scenario.get("image") != "postgres:16-alpine"
        or not str(scenario.get("server_version_num", "")).startswith("16")
        or scenario.get("alembic_current") != scenario.get("alembic_head")
        or scenario.get("warning_hits") != []
    ):
        raise NativeEvidenceError("native_schema_or_warning_invalid")
    receipt = scenario.get("test_receipt")
    if not isinstance(receipt, dict):
        raise NativeEvidenceError("native_test_receipt_required")
    witnesses = _strings(source.get("required_witnesses"), "native_witnesses_required")
    registered = _strings(source.get("selected_node_ids"), "native_selection_required")
    digest = _digest(source.get("selected_sha256"), "native_selection_hash_required")
    return _scenario(receipt, witnesses, registered, digest)


def native_acceptance(
    candidate: str,
    binding: Path,
    binding_seal: Path,
    candidate_manifest: Path,
    native_source_path: Path,
    native_run_manifest: Path,
    native_log: Path,
    cleanup_index: Path,
) -> JsonObject:
    source = load(native_source_path)
    _require(source.get("candidate") == candidate, "native_source_candidate_mismatch")
    accepted: dict[str, JsonValue] = {
        "status": "verified",
        "candidate": candidate,
        "selected": canonical_selected(native_run_manifest, source),
        "binding": file_tuple(binding),
        "binding_seal": file_tuple(binding_seal),
        "candidate_manifest": file_tuple(candidate_manifest),
        "native_source_manifest": file_tuple(native_source_path),
        "native_run_manifest": file_tuple(native_run_manifest),
        "native_log": file_tuple(native_log),
        "cleanup_index": file_tuple(cleanup_index),
    }
    cleanup = verify_cleanup(cleanup_index, "native", native_log)
    return accepted | _source_sidecars(source, cleanup)
