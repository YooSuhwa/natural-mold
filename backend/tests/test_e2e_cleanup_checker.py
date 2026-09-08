"""Independent validation contracts for isolated E2E cleanup manifests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import e2e_cleanup_lifecycle as lifecycle  # noqa: E402
from e2e_cleanup_checker import (  # noqa: E402
    ManifestValidationError,
    load_and_validate,
    validate_payload,
)

_LIVE_NODES = [
    (
        "live-manual::e2e/agent-live-quality.spec.ts::"
        "follows a bounded instruction through a live model chat"
    ),
    (
        "live-manual::e2e/agent-triggers.spec.ts::"
        "a created interval trigger renders in the settings triggers tab"
    ),
    (
        "live-manual::e2e/builder.spec.ts::"
        "starts a session and runs the build pipeline from an initial message"
    ),
    "live-manual::e2e/operator-screens.spec.ts::System LLM shows the seed-configured role slots",
    (
        "live-manual::e2e/operator-screens.spec.ts::"
        "creates and deletes a system credential through the catalog modal"
    ),
]


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_export(repository: Path, project: str) -> tuple[str, list[dict[str, object]]]:
    """Create a persistent exporter-compatible receipt directory."""
    (repository / ".gitignore").write_text("output/\n")
    relative = "output/e2e-captures/20260831-checker"
    directory = repository / relative
    directory.mkdir(parents=True)
    artifact = b'{"result":"passed"}\n'
    artifact_file = {
        "path": "results/execution.json",
        "sha256": _sha256(artifact),
        "size_bytes": len(artifact),
    }
    exporter_manifest = {
        "schema_version": 1,
        "project": project,
        "policy": {
            "version": 1,
            "screenshots": "scripted-capture-only",
            "max_file_bytes": 20 * 1024 * 1024,
            "max_total_bytes": 50 * 1024 * 1024,
        },
        "secret_scan": {"passed": True, "exact_secret_count": 0},
        "files": [artifact_file],
        "total": {"file_count": 1, "size_bytes": len(artifact)},
    }
    manifest_content = (json.dumps(exporter_manifest, sort_keys=True) + "\n").encode()
    manifest_file = {
        "path": "export-manifest.json",
        "sha256": _sha256(manifest_content),
        "size_bytes": len(manifest_content),
    }
    (directory / "results").mkdir()
    (directory / artifact_file["path"]).write_bytes(artifact)
    (directory / "export-manifest.json").write_bytes(manifest_content)
    return relative, [manifest_file, artifact_file]


def _manifest(
    repository: Path, *, lane: str = "scripted", project: str = "scripted-smoke"
) -> dict[str, object]:
    export_directory, files = _write_export(repository, project)
    return {
        "schema_version": 1,
        "runner": "moldy-isolated-e2e",
        "lane": lane,
        "project": project,
        "workers": 1,
        "retries": 0,
        "reuse_existing_server": False,
        "status": "passed",
        "failure_reason": None,
        "child_exit_code": 0,
        "self_test": "normal",
        "run_id": "a" * 24,
        "owned_run_root": True,
        "owned_database": True,
        "owned_backend": True,
        "owned_frontend": True,
        "owned_proxy": lane == "live",
        "postgres_image": "postgres:16-alpine",
        "server_version_num": "160001",
        "alembic_head": "m70",
        "alembic_current": "m70",
        "schema_fingerprint": "b" * 64,
        "second_upgrade_idempotent": True,
        "frontend_port": 3100 if lane == "scripted" else 3200,
        "backend_port": 8101 if lane == "scripted" else 8201,
        "selected_ids": ["scripted-smoke::e2e/smoke.spec.ts::works"],
        "executed_ids": ["scripted-smoke::e2e/smoke.spec.ts::works"],
        "unexpected_failures": [],
        "export": {
            "schema_version": 1,
            "secret_scan_passed": True,
            "export_directory": export_directory,
            "manifest": files[0],
            "files": files,
            "screenshots": [],
        },
        "egress": {"enabled": False, "clean_stop": True, "records": []},
        "cleanup": {
            "cleanup_container_removed": True,
            "owned_label_absent": True,
            "postgres_port_removed": True,
            "owned_database_removed": True,
            "backend_port_removed": True,
            "frontend_port_removed": True,
            "proxy_port_removed": True,
            "process_group_stopped": True,
            "cleanup_run_root_removed": True,
            "foreign_containers_preserved": True,
        },
    }


def _preexecution_manifest(repository: Path) -> dict[str, object]:
    """Build the only export-free receipt allowed before scripted-smoke execution."""
    payload = _manifest(repository)
    shutil.rmtree(repository / "output")
    payload.update(
        {
            "status": "failed",
            "failure_reason": "node_major_mismatch",
            "child_exit_code": 70,
            "owned_run_root": True,
            "owned_database": False,
            "owned_backend": False,
            "owned_frontend": False,
            "owned_proxy": False,
            "server_version_num": None,
            "alembic_head": None,
            "alembic_current": None,
            "schema_fingerprint": None,
            "second_upgrade_idempotent": False,
            "selected_ids": [],
            "executed_ids": [],
            "unexpected_failures": [],
            "export": {
                "schema_version": 1,
                "secret_scan_passed": False,
                "failure_code": None,
                "export_directory": None,
                "manifest": None,
                "files": [],
                "screenshots": [],
                "source_rejection": None,
            },
        }
    )
    cleanup = payload["cleanup"]
    assert isinstance(cleanup, dict)
    cleanup["foreign_containers_preserved"] = None
    return payload


def _selection_failure_manifest(repository: Path) -> dict[str, object]:
    """Build a post-provision collection failure with exported bounded receipts."""
    payload = _manifest(repository)
    export = payload["export"]
    assert isinstance(export, dict)
    directory = repository / str(export["export_directory"])
    exporter_manifest_path = directory / "export-manifest.json"
    exporter_manifest = json.loads(exporter_manifest_path.read_text())
    artifact_entries = exporter_manifest["files"]
    assert isinstance(artifact_entries, list)
    for relative, content in (
        ("results/selection.json", b'{"suites":[]}\n'),
        ("results/selection.log", b"Playwright collection failed before execution.\n"),
    ):
        target = directory.joinpath(*relative.split("/"))
        target.write_bytes(content)
        artifact_entries.append(
            {
                "path": relative,
                "sha256": _sha256(content),
                "size_bytes": len(content),
            }
        )
    artifact_entries.sort(key=lambda item: str(item["path"]))
    exporter_manifest["total"] = {
        "file_count": len(artifact_entries),
        "size_bytes": sum(int(item["size_bytes"]) for item in artifact_entries),
    }
    manifest_content = (json.dumps(exporter_manifest, sort_keys=True) + "\n").encode()
    exporter_manifest_path.write_bytes(manifest_content)
    manifest_entry = {
        "path": "export-manifest.json",
        "sha256": _sha256(manifest_content),
        "size_bytes": len(manifest_content),
    }
    export["manifest"] = manifest_entry
    export["files"] = [manifest_entry, *artifact_entries]
    payload.update(
        {
            "status": "failed",
            "failure_reason": "playwright_list_failed",
            "child_exit_code": 70,
            "selected_ids": [],
            "executed_ids": [],
            "unexpected_failures": [],
            "owned_run_root": True,
            "owned_database": True,
            "owned_backend": False,
            "owned_frontend": False,
            "owned_proxy": False,
        }
    )
    return payload


def _execution_startup_failure_manifest(repository: Path) -> dict[str, object]:
    """Build a Playwright web-server failure after selection but before test execution."""
    payload = _manifest(repository)
    export = payload["export"]
    assert isinstance(export, dict)
    directory = repository / str(export["export_directory"])
    exporter_manifest_path = directory / "export-manifest.json"
    exporter_manifest = json.loads(exporter_manifest_path.read_text())
    artifact_entries = exporter_manifest["files"]
    assert isinstance(artifact_entries, list)
    for relative, content in (
        ("results/selection.json", b'{"suites":[{"title":"smoke"}]}\n'),
        ("results/selection.log", b"One scripted smoke test selected.\n"),
        ("results/execution.log", b"Playwright stopped before its first test.\n"),
        ("results/execution.stderr.log", b"Web server startup timed out.\n"),
    ):
        target = directory.joinpath(*relative.split("/"))
        target.write_bytes(content)
        artifact_entries.append(
            {
                "path": relative,
                "sha256": _sha256(content),
                "size_bytes": len(content),
            }
        )
    artifact_entries.sort(key=lambda item: str(item["path"]))
    exporter_manifest["total"] = {
        "file_count": len(artifact_entries),
        "size_bytes": sum(int(item["size_bytes"]) for item in artifact_entries),
    }
    manifest_content = (json.dumps(exporter_manifest, sort_keys=True) + "\n").encode()
    exporter_manifest_path.write_bytes(manifest_content)
    manifest_entry = {
        "path": "export-manifest.json",
        "sha256": _sha256(manifest_content),
        "size_bytes": len(manifest_content),
    }
    export["manifest"] = manifest_entry
    export["files"] = [manifest_entry, *artifact_entries]
    payload.update(
        {
            "status": "failed",
            "failure_reason": "playwright_failed",
            "child_exit_code": 1,
            "executed_ids": [],
            "unexpected_failures": [],
            "owned_backend": True,
            "owned_frontend": True,
        }
    )
    return payload


def test_validate_payload_accepts_scripted_smoke_preexecution_diagnostic(tmp_path: Path) -> None:
    """Given an empty controlled pre-execution receipt, when checked, then it remains failed."""
    payload = _preexecution_manifest(tmp_path)

    scope = validate_payload(payload, repository_root=tmp_path)

    assert payload["status"] == "failed"
    assert scope == "manifest-only"


def test_validate_payload_accepts_exported_scripted_smoke_selection_failure(
    tmp_path: Path,
) -> None:
    payload = _selection_failure_manifest(tmp_path)

    scope = validate_payload(payload, repository_root=tmp_path)

    assert payload["status"] == "failed"
    assert scope == "full"


def test_validate_payload_accepts_exported_execution_startup_failure(tmp_path: Path) -> None:
    payload = _execution_startup_failure_manifest(tmp_path)

    scope = validate_payload(payload, repository_root=tmp_path)

    assert payload["selected_ids"]
    assert payload["executed_ids"] == []
    assert scope == "full"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("child_exit_code", 1),
        ("selected_ids", ["scripted-smoke::e2e/smoke.spec.ts::works"]),
        ("owned_database", False),
        ("owned_backend", True),
        ("unexpected_key", "unexpected"),
    ],
)
def test_validate_payload_rejects_invalid_selection_failure_contract(
    tmp_path: Path, field: str, value: object
) -> None:
    payload = _selection_failure_manifest(tmp_path)
    payload[field] = value

    with pytest.raises(ManifestValidationError):
        validate_payload(payload, repository_root=tmp_path)


def test_validate_payload_rejects_selection_failure_without_collection_receipts(
    tmp_path: Path,
) -> None:
    payload = _manifest(tmp_path)
    payload.update(
        {
            "status": "failed",
            "failure_reason": "playwright_list_failed",
            "child_exit_code": 70,
            "selected_ids": [],
            "executed_ids": [],
            "unexpected_failures": [],
            "owned_backend": False,
            "owned_frontend": False,
        }
    )

    with pytest.raises(ManifestValidationError, match="selection_artifacts"):
        validate_payload(payload, repository_root=tmp_path)


def test_validate_payload_rejects_selection_failure_with_incomplete_cleanup(
    tmp_path: Path,
) -> None:
    payload = _selection_failure_manifest(tmp_path)
    cleanup = payload["cleanup"]
    assert isinstance(cleanup, dict)
    cleanup["cleanup_run_root_removed"] = False

    with pytest.raises(ManifestValidationError, match="cleanup"):
        validate_payload(payload, repository_root=tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("failure_reason", "password=not-for-a-receipt"),
        ("failure_reason", "sk-abcdefghijklmnop"),
        ("selected_ids", ["scripted-smoke::e2e/smoke.spec.ts::works"]),
        ("attempt_id", "a" * 64),
        ("owned_database", True),
    ],
)
def test_validate_payload_rejects_untrusted_preexecution_diagnostic_values(
    tmp_path: Path, field: str, value: str | list[str] | bool
) -> None:
    """Given one unsafe pre-execution field, when checked, then it cannot become a receipt."""
    payload = _preexecution_manifest(tmp_path)
    payload[field] = value

    with pytest.raises(ManifestValidationError):
        validate_payload(payload, repository_root=tmp_path)


@pytest.mark.parametrize(
    "mutation",
    ["malformed_export", "incomplete_cleanup"],
)
def test_validate_payload_rejects_incomplete_preexecution_receipts(
    tmp_path: Path, mutation: str
) -> None:
    """Given a non-exact empty export or cleanup claim, when checked, then it fails closed."""
    payload = _preexecution_manifest(tmp_path)
    if mutation == "malformed_export":
        export = payload["export"]
        assert isinstance(export, dict)
        export["secret_scan_passed"] = True
    elif mutation == "incomplete_cleanup":
        cleanup = payload["cleanup"]
        assert isinstance(cleanup, dict)
        cleanup["cleanup_run_root_removed"] = False
    with pytest.raises(ManifestValidationError):
        validate_payload(payload, repository_root=tmp_path)


@pytest.mark.parametrize(
    ("target", "field"),
    [("export", "unexpected"), ("export", "attempt_id"), ("top", "attempt_id")],
)
def test_validate_payload_rejects_extra_and_mixed_export_schema(
    tmp_path: Path, target: str, field: str
) -> None:
    payload = _manifest(tmp_path)
    if target == "export":
        export = payload["export"]
        assert isinstance(export, dict)
        export[field] = None
    else:
        payload[field] = None

    with pytest.raises(ManifestValidationError, match="export_schema"):
        validate_payload(payload, repository_root=tmp_path)


def test_validate_payload_rejects_legacy_schema_in_final_attempt_namespace(
    tmp_path: Path,
) -> None:
    payload = _manifest(tmp_path, project="scripted-full")
    export = payload["export"]
    assert isinstance(export, dict)
    old_directory = tmp_path / str(export["export_directory"])
    final_relative = "output/e2e-captures/20260905-runtime-policy-final-" + "a" * 64 + "-scripted"
    final_directory = tmp_path / final_relative
    old_directory.rename(final_directory)
    export["export_directory"] = final_relative

    with pytest.raises(ManifestValidationError, match="final_attempt_export"):
        validate_payload(payload, repository_root=tmp_path)


def _final_f2_manifest(
    repository: Path, node: str, project: str, requested_specs: tuple[str, ...]
) -> tuple[dict[str, object], Path]:
    payload = _manifest(repository, project=project)
    attempt_id = "a" * 64
    token = "c" * 16
    export = payload["export"]
    assert isinstance(export, dict)
    original = repository / str(export["export_directory"])
    relative = f"output/e2e-captures/20260905-runtime-policy-final-{attempt_id}-f2-{token}"
    destination = repository / relative
    original.rename(destination)
    files = export["files"]
    assert isinstance(files, list)
    tree_payload = json.dumps(files, separators=(",", ":")).encode()
    tree_hash = hashlib.sha256(tree_payload).hexdigest()
    export.update(
        failure_code=None,
        export_directory=relative,
        attempt_id=attempt_id,
        export_directory_absolute=str(destination),
        export_tree_sha256=tree_hash,
        screenshots_absolute=[],
        source_rejection=None,
    )
    selected = [f"{project}::{spec}::works" for spec in requested_specs]
    if not selected:
        selected = [f"{project}::e2e/smoke.spec.ts::works"]
    payload.update(
        attempt_id=attempt_id,
        head_sha="b" * 40,
        requested_specs=list(requested_specs),
        skipped_ids=[],
        export_directory_absolute=str(destination),
        export_tree_sha256=tree_hash,
        screenshots=[],
        selected_ids=selected,
        executed_ids=selected,
    )
    receipt = repository / "final-attempts" / attempt_id / f"f2-static.{node}.{token}.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    receipt.chmod(0o600)
    return payload, receipt


@pytest.mark.parametrize(
    ("node", "project", "requested_specs"),
    [
        (
            "todo21-runtime-policy-e2e",
            "scripted-full",
            (
                "e2e/agent-settings.spec.ts",
                "e2e/runtime-todo-policy.spec.ts",
                "e2e/runtime-filesystem-policy.spec.ts",
                "e2e/chat-compaction.spec.ts",
            ),
        ),
        (
            "todo21-visual-capture",
            "scripted-capture",
            (
                "e2e/agent-settings.spec.ts",
                "e2e/runtime-todo-policy.spec.ts",
                "e2e/runtime-filesystem-policy.spec.ts",
                "e2e/chat-compaction.spec.ts",
            ),
        ),
    ],
)
def test_checker_accepts_exact_f2_e2e_receipt_binding(
    tmp_path: Path, node: str, project: str, requested_specs: tuple[str, ...]
) -> None:
    payload, receipt = _final_f2_manifest(tmp_path, node, project, requested_specs)
    load_and_validate(receipt, repository_root=tmp_path)


@pytest.mark.parametrize(
    "mutation", ["top_key", "export_key", "duplicate_skip", "nonempty_skip", "receipt"]
)
def test_final_checker_rejects_schema_skip_and_binding_mutations(
    tmp_path: Path, mutation: str
) -> None:
    specs = (
        "e2e/agent-settings.spec.ts",
        "e2e/runtime-todo-policy.spec.ts",
        "e2e/runtime-filesystem-policy.spec.ts",
        "e2e/chat-compaction.spec.ts",
    )
    payload, receipt = _final_f2_manifest(
        tmp_path, "todo21-runtime-policy-e2e", "scripted-full", specs
    )
    skipped = "scripted-full::e2e/skipped.spec.ts::skipped case"
    if mutation == "top_key":
        payload["unknown"] = True
    elif mutation == "export_key":
        export = payload["export"]
        assert isinstance(export, dict)
        export["unknown"] = True
    elif mutation == "duplicate_skip":
        payload["skipped_ids"] = [skipped, skipped]
    elif mutation == "nonempty_skip":
        payload["skipped_ids"] = [skipped]
    else:
        receipt = receipt.with_name("f2-static.todo21-visual-capture." + "c" * 16 + ".json")
    with pytest.raises(ManifestValidationError):
        validate_payload(payload, repository_root=tmp_path, receipt_path=receipt)


def _replace_export_with_source_rejection(
    repository: Path, payload: dict[str, object]
) -> dict[str, object]:
    export = payload["export"]
    assert isinstance(export, dict)
    directory = repository / str(export["export_directory"])
    shutil.rmtree(directory / "results")
    rejection: dict[str, object] = {
        "category": "secret_scan",
        "rule_id": "sensitive_assignment",
        "artifact_path": "results/execution.log",
        "tests": [],
    }
    manifest_path = directory / "export-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_rejection"] = rejection
    manifest["files"] = []
    manifest["total"] = {"file_count": 0, "size_bytes": 0}
    content = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    manifest_path.write_bytes(content)
    manifest_file = {
        "path": "export-manifest.json",
        "sha256": _sha256(content),
        "size_bytes": len(content),
    }
    export["manifest"] = manifest_file
    export["files"] = [manifest_file]
    export["source_rejection"] = rejection
    return rejection


def test_validate_payload_accepts_persistent_scripted_success(tmp_path: Path) -> None:
    # Given a logical E2E success with a hash-valid persistent export.
    payload = _manifest(tmp_path)

    # When the independent checker validates it.
    validate_payload(payload, repository_root=tmp_path)

    # Then the cleanup and export guarantees are accepted.


def test_validate_payload_accepts_legacy_success_without_failure_diagnostics(
    tmp_path: Path,
) -> None:
    payload = _manifest(tmp_path)
    del payload["unexpected_failures"]

    validate_payload(payload, repository_root=tmp_path)


def test_validate_payload_accepts_fail_closed_artifact_source_rejection(tmp_path: Path) -> None:
    payload = _manifest(tmp_path)
    _replace_export_with_source_rejection(tmp_path, payload)
    payload.update(
        {
            "status": "failed",
            "failure_reason": "artifact_export_failed",
            "child_exit_code": 0,
        }
    )

    validate_payload(payload, repository_root=tmp_path)


def test_validate_payload_rejects_passed_run_with_artifact_source_rejection(
    tmp_path: Path,
) -> None:
    payload = _manifest(tmp_path)
    _replace_export_with_source_rejection(tmp_path, payload)

    with pytest.raises(ManifestValidationError):
        validate_payload(payload, repository_root=tmp_path)


def test_validate_payload_rejects_playwright_failure_without_own_diagnostics(
    tmp_path: Path,
) -> None:
    payload = _manifest(tmp_path)
    _replace_export_with_source_rejection(tmp_path, payload)
    payload.update(
        {
            "status": "failed",
            "failure_reason": "playwright_failed",
            "child_exit_code": 1,
        }
    )

    with pytest.raises(ManifestValidationError, match="failure_diagnostics"):
        validate_payload(payload, repository_root=tmp_path)


def test_validate_payload_accepts_exporter_smoke_receipt_without_playwright_dotfile(
    tmp_path: Path,
) -> None:
    # Given a real exporter input containing Playwright's internal dotfile.
    payload = _manifest(tmp_path)
    run_root = tmp_path / ".moldy-test-run.dotfile-export"
    result_directory = run_root / "frontend/test-results/scripted-smoke"
    result_directory.mkdir(parents=True)
    (result_directory / "junit.xml").write_text("<testsuite/>")
    internal = result_directory / "playwright-artifacts/.last-run.json"
    internal.parent.mkdir()
    internal.write_text('{"status":"passed"}')
    receipt_path = run_root / "export-receipt.json"
    exporter = Path(__file__).resolve().parents[2] / "frontend/scripts/export-e2e-artifacts.mjs"
    node = shutil.which("node")
    assert node is not None
    result = subprocess.run(
        [
            node,
            str(exporter),
            "--run-root",
            str(run_root),
            "--source-dir",
            str(result_directory),
            "--project",
            "scripted-smoke",
            "--slug",
            "dotfile-export",
            "--repo-root",
            str(tmp_path),
            "--receipt",
            str(receipt_path),
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    payload["export"] = receipt
    payload["attempt_id"] = receipt["attempt_id"]
    payload["export_directory_absolute"] = receipt["export_directory_absolute"]
    payload["export_tree_sha256"] = receipt["export_tree_sha256"]
    payload["screenshots"] = receipt["screenshots_absolute"]

    # When the passed smoke manifest is independently checked.
    validate_payload(payload, repository_root=tmp_path)

    # Then the internal dotfile is neither persisted nor declared.
    paths = [entry["path"] for entry in receipt["files"]]
    assert "results/playwright-artifacts/.last-run.json" not in paths
    assert not (
        tmp_path / str(receipt["export_directory"]) / "results/playwright-artifacts/.last-run.json"
    ).exists()


def test_validate_payload_rejects_cross_lane_project_pairing(tmp_path: Path) -> None:
    # Given a live project asserted as a scripted lane.
    payload = _manifest(tmp_path, project="live-manual")

    # When the receipt is checked, then pairing cannot be forged.
    with pytest.raises(ManifestValidationError, match="project_lane"):
        validate_payload(payload, repository_root=tmp_path)


def test_validate_payload_accepts_exact_live_cases(tmp_path: Path) -> None:
    # Given the live lane's exact selected and executed cases plus aggregate egress.
    payload = _manifest(tmp_path, lane="live", project="live-manual")
    nodes = list(_LIVE_NODES)
    payload.update(
        {
            "selected_ids": nodes,
            "executed_ids": nodes,
            "egress": {
                "enabled": True,
                "clean_stop": True,
                "records": [
                    {
                        "method": "POST",
                        "origin": "https://api.example.com",
                        "path_class": "chat_completions",
                        "status": 200,
                        "count": 1,
                    },
                    {
                        "method": "POST",
                        "origin": "https://api.example.com",
                        "path_class": "chat_completions",
                        "status": 429,
                        "count": 2,
                    },
                ],
            },
        }
    )

    # When the bounded live receipt is checked.
    validate_payload(payload, repository_root=tmp_path)

    # Then the exact-case and aggregate-only policy is accepted.


def test_validate_payload_rejects_live_egress_with_only_rate_limits(tmp_path: Path) -> None:
    payload = _manifest(tmp_path, lane="live", project="live-manual")
    nodes = list(_LIVE_NODES)
    payload.update(
        {
            "selected_ids": nodes,
            "executed_ids": nodes,
            "egress": {
                "enabled": True,
                "clean_stop": True,
                "records": [
                    {
                        "method": "POST",
                        "origin": "https://api.example.com",
                        "path_class": "chat_completions",
                        "status": 429,
                        "count": 2,
                    }
                ],
            },
        }
    )

    with pytest.raises(ManifestValidationError, match="egress_record"):
        validate_payload(payload, repository_root=tmp_path)


def test_validate_payload_rejects_forged_live_set(tmp_path: Path) -> None:
    # Given nodes that have the right count but one is not in the approved live inventory.
    payload = _manifest(tmp_path, lane="live", project="live-manual")
    nodes = [*_LIVE_NODES[:-1], "live-manual::e2e/forged.spec.ts::not an approved live node"]
    payload["selected_ids"] = nodes
    payload["executed_ids"] = nodes
    payload["egress"] = {
        "enabled": True,
        "clean_stop": True,
        "records": [
            {
                "method": "POST",
                "origin": "https://api.example.com",
                "path_class": "chat_completions",
                "status": 200,
                "count": 1,
            }
        ],
    }

    # When the checker evaluates the exact live inventory.
    with pytest.raises(ManifestValidationError, match="live_selection"):
        validate_payload(payload, repository_root=tmp_path)

    # Then matching only the count is insufficient.


def test_validate_payload_rejects_non_smoke_scripted_smoke_node(tmp_path: Path) -> None:
    # Given a scripted-smoke receipt that names a different spec.
    payload = _manifest(tmp_path)
    nodes = ["scripted-smoke::e2e/unrelated.spec.ts::forged smoke"]
    payload["selected_ids"] = nodes
    payload["executed_ids"] = nodes

    # When the project-specific smoke prefix is checked.
    with pytest.raises(ManifestValidationError, match="smoke_selection"):
        validate_payload(payload, repository_root=tmp_path)

    # Then generic e2e paths cannot stand in for smoke coverage.


def test_validate_payload_accepts_manifest_first_scripted_capture_export(tmp_path: Path) -> None:
    # Given the exporter order: manifest first, then artifact paths in lexical order.
    payload = _manifest(tmp_path, project="scripted-capture")
    nodes = ["scripted-capture::e2e/captures/dashboard.spec.ts::captures dashboard"]
    payload["selected_ids"] = nodes
    payload["executed_ids"] = nodes
    export = payload["export"]
    assert isinstance(export, dict)
    files = export["files"]
    assert isinstance(files, list)
    manifest_entry = files[0]
    artifact_entry = files[1]
    assert isinstance(manifest_entry, dict) and isinstance(artifact_entry, dict)
    directory = tmp_path / str(export["export_directory"])
    capture_content = b"scripted capture"
    capture_path = "captures/wave-7/dashboard.png"
    (directory / "captures/wave-7").mkdir(parents=True)
    (directory / capture_path).write_bytes(capture_content)
    capture_entry = {
        "path": capture_path,
        "sha256": _sha256(capture_content),
        "size_bytes": len(capture_content),
    }
    exporter_manifest = json.loads((directory / "export-manifest.json").read_text())
    exporter_manifest["project"] = "scripted-capture"
    exporter_manifest["files"] = [capture_entry, artifact_entry]
    exporter_manifest["total"] = {
        "file_count": 2,
        "size_bytes": len(capture_content) + int(artifact_entry["size_bytes"]),
    }
    content = (json.dumps(exporter_manifest, sort_keys=True) + "\n").encode()
    (directory / "export-manifest.json").write_bytes(content)
    manifest_entry["sha256"] = _sha256(content)
    manifest_entry["size_bytes"] = len(content)
    export["files"] = [manifest_entry, capture_entry, artifact_entry]
    export["screenshots"] = [capture_path]

    # When the checker reads the persisted capture tree.
    validate_payload(payload, repository_root=tmp_path)

    # Then its manifest-first receipt order matches the exporter contract.


@pytest.mark.parametrize("mutation", ["hash", "extra_file", "symlink", "hardlink"])
def test_validate_payload_rejects_export_tampering(tmp_path: Path, mutation: str) -> None:
    # Given one altered persistent export invariant.
    payload = _manifest(tmp_path)
    export = payload["export"]
    assert isinstance(export, dict)
    directory = tmp_path / str(export["export_directory"])
    if mutation == "hash":
        files = export["files"]
        assert isinstance(files, list)
        first = files[0]
        assert isinstance(first, dict)
        first["sha256"] = "c" * 64
    elif mutation == "extra_file":
        (directory / "unlisted.txt").write_text("unexpected")
    elif mutation == "symlink":
        (directory / "linked.txt").symlink_to(directory / "export-manifest.json")
    else:
        os.link(directory / "export-manifest.json", directory / "hard-linked.json")

    # When validation reads the immutable export tree, then tampering is rejected.
    with pytest.raises(ManifestValidationError):
        validate_payload(payload, repository_root=tmp_path)


def test_validate_payload_fails_closed_on_export_ancestor_swap_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given a valid export and a deterministic rename-away/replace/restore traversal attack.
    payload = _manifest(tmp_path)
    export = payload["export"]
    assert isinstance(export, dict)
    directory = tmp_path / str(export["export_directory"])
    replacement = directory.with_name(f"{directory.name}-attacker")
    saved = directory.with_name(f"{directory.name}-saved")
    replacement.mkdir()
    marker = replacement / "attacker-marker.txt"
    marker.write_text("must remain untouched")
    monkeypatch.setenv(
        "MOLDY_E2E_FS_TEST_SWAP_JSON",
        json.dumps(
            {
                "phase": "checker_walk",
                "target": str(directory),
                "replacement": str(replacement),
                "saved": str(saved),
            }
        ),
    )

    # When descriptor-relative traversal reaches the synchronized swap-back point.
    with pytest.raises(ManifestValidationError, match="export_tree"):
        validate_payload(payload, repository_root=tmp_path)

    # Then the checker rejects the receipt and the attacker tree was never traversed.
    assert marker.read_text() == "must remain untouched"
    assert not saved.exists()
    assert (directory / "export-manifest.json").is_file()


def test_validate_payload_rejects_secret_shaped_persistent_content(tmp_path: Path) -> None:
    # Given a listed file whose checksum matches but whose content resembles a secret.
    payload = _manifest(tmp_path)
    export = payload["export"]
    assert isinstance(export, dict)
    files = export["files"]
    assert isinstance(files, list)
    artifact = files[1]
    assert isinstance(artifact, dict)
    content = b"authorization=Bearer not-a-real-token\n"
    directory = tmp_path / str(export["export_directory"])
    (directory / "results/execution.json").write_bytes(content)
    artifact["sha256"] = _sha256(content)
    artifact["size_bytes"] = len(content)
    manifest = files[0]
    assert isinstance(manifest, dict)
    manifest_content = (directory / "export-manifest.json").read_text()
    updated = json.loads(manifest_content)
    updated_file = updated["files"][0]
    updated_file.update(artifact)
    updated["total"]["size_bytes"] = len(content)
    encoded = (json.dumps(updated, sort_keys=True) + "\n").encode()
    (directory / "export-manifest.json").write_bytes(encoded)
    manifest["sha256"] = _sha256(encoded)
    manifest["size_bytes"] = len(encoded)

    # When the checker independently reads the exported bytes.
    with pytest.raises(ManifestValidationError, match="secret_material"):
        validate_payload(payload, repository_root=tmp_path)


@pytest.mark.parametrize("mutation", ["receipt_size", "actual_size"])
def test_validate_payload_rejects_per_file_export_size_before_read(
    tmp_path: Path, mutation: str
) -> None:
    # Given a file whose receipt or lstat size exceeds the exporter policy before content is read.
    payload = _manifest(tmp_path)
    export = payload["export"]
    assert isinstance(export, dict)
    files = export["files"]
    assert isinstance(files, list)
    artifact = files[1]
    assert isinstance(artifact, dict)
    if mutation == "receipt_size":
        artifact["size_bytes"] = 20 * 1024 * 1024 + 1
    else:
        directory = tmp_path / str(export["export_directory"])
        with (directory / "results/execution.json").open("r+b") as target:
            target.truncate(20 * 1024 * 1024 + 1)

    # When persistent export limits are enforced.
    with pytest.raises(ManifestValidationError, match="export_file_size"):
        validate_payload(payload, repository_root=tmp_path)

    # Then the checker fails before accepting an oversized artifact.


def test_validate_payload_rejects_export_total_size_before_tree_read(tmp_path: Path) -> None:
    # Given three individually bounded files whose declared aggregate exceeds the export policy.
    payload = _manifest(tmp_path)
    export = payload["export"]
    assert isinstance(export, dict)
    files = export["files"]
    assert isinstance(files, list)
    directory = tmp_path / str(export["export_directory"])
    artifact = files[1]
    assert isinstance(artifact, dict)
    artifact["size_bytes"] = 20 * 1024 * 1024
    for name in ("second.json", "third.json"):
        target = directory / "results" / name
        target.write_text("bounded source")
        files.append(
            {
                "path": f"results/{name}",
                "sha256": _sha256(target.read_bytes()),
                "size_bytes": 20 * 1024 * 1024,
            }
        )

    # When the total receipt size is evaluated before the export tree is read.
    with pytest.raises(ManifestValidationError, match="export_total_size"):
        validate_payload(payload, repository_root=tmp_path)

    # Then aggregate memory pressure cannot be hidden behind individually valid entries.


@pytest.mark.parametrize(
    ("self_test", "status", "reason", "exit_code"),
    [
        ("spec-failure", "failed", "playwright_list_failed", 70),
        ("server-failure", "failed", "server_start_failed", 1),
        ("dsn-failure", "failed", "dsn_mismatch", 70),
        ("sigint", "interrupted", "signal", 130),
    ],
)
def test_validate_payload_accepts_expected_self_test_receipts(
    tmp_path: Path, self_test: str, status: str, reason: str, exit_code: int
) -> None:
    # Given an expected forced-failure or SIGINT receipt with cleanup/export proof.
    payload = _manifest(tmp_path)
    payload.update(
        {
            "self_test": self_test,
            "status": status,
            "failure_reason": reason,
            "child_exit_code": exit_code,
            "selected_ids": [],
            "executed_ids": [],
        }
    )

    # When its bounded failure semantics are checked.
    validate_payload(payload, repository_root=tmp_path)

    # Then the deliberate receipt remains checker-valid.


@pytest.mark.parametrize("exit_code", [0, 130])
def test_validate_payload_rejects_server_failure_without_bounded_server_start_exit(
    tmp_path: Path, exit_code: int
) -> None:
    # Given a server-failure receipt with a successful or signal-shaped child exit.
    payload = _manifest(tmp_path)
    payload.update(
        {
            "self_test": "server-failure",
            "status": "failed",
            "failure_reason": "server_start_failed",
            "child_exit_code": exit_code,
            "selected_ids": [],
            "executed_ids": [],
        }
    )

    # When the failure result is checked.
    with pytest.raises(ManifestValidationError, match="self_test"):
        validate_payload(payload, repository_root=tmp_path)

    # Then only a bounded non-signal server-start failure is accepted.


def test_validate_payload_accepts_normal_playwright_failure_receipt(tmp_path: Path) -> None:
    # Given a normal E2E run that selected and executed one case before Playwright failed.
    payload = _manifest(tmp_path)
    payload.update(
        {
            "status": "failed",
            "failure_reason": "playwright_failed",
            "child_exit_code": 1,
            "unexpected_failures": [
                {
                    "node_id": "scripted-smoke::e2e/smoke.spec.ts::works",
                    "status": "failed",
                    "location": {
                        "file": "e2e/smoke.spec.ts",
                        "line": 10,
                        "column": 2,
                    },
                    "network_failure_codes": [
                        "api_request_failure",
                        "other_response_failure",
                    ],
                    "failure_phase": "verify_error_collectors",
                }
            ],
        }
    )

    # When the normal failure receipt is checked.
    validate_payload(payload, repository_root=tmp_path)

    # Then the non-forced Playwright failure remains checker-valid.


def test_validate_payload_rejects_normal_playwright_failure_without_diagnostics(
    tmp_path: Path,
) -> None:
    payload = _manifest(tmp_path)
    payload.update(
        {
            "status": "failed",
            "failure_reason": "playwright_failed",
            "child_exit_code": 1,
        }
    )
    del payload["unexpected_failures"]

    with pytest.raises(ManifestValidationError, match="failure_diagnostics"):
        validate_payload(payload, repository_root=tmp_path)


@pytest.mark.parametrize(
    "unexpected",
    [
        [
            {
                "node_id": "scripted-smoke::e2e/smoke.spec.ts::works",
                "status": "failed",
                "extra": 1,
            }
        ],
        [
            {
                "node_id": "scripted-smoke::e2e/smoke.spec.ts::works",
                "status": "failed",
                "location": {
                    "file": "/tmp/secret.spec.ts",
                    "line": 1,
                    "column": 1,
                },
            }
        ],
        [
            {
                "node_id": "scripted-smoke::e2e/smoke.spec.ts::works",
                "status": "failed",
                "location": {
                    "file": "e2e/other-safe.spec.ts",
                    "line": 1,
                    "column": 1,
                },
            }
        ],
        [
            {
                "node_id": "scripted-smoke::e2e/smoke.spec.ts::works",
                "status": "failed",
                "location": {
                    "file": "e2e/smoke.spec.ts",
                    "line": True,
                    "column": 1,
                },
            }
        ],
    ],
)
def test_validate_payload_rejects_malformed_unexpected_failure_diagnostics(
    tmp_path: Path, unexpected: list[dict[str, object]]
) -> None:
    payload = _manifest(tmp_path)
    payload.update(
        {
            "status": "failed",
            "failure_reason": "playwright_failed",
            "child_exit_code": 1,
        }
    )
    payload["unexpected_failures"] = unexpected

    with pytest.raises(ManifestValidationError, match="failure_diagnostics"):
        validate_payload(payload, repository_root=tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "passed"),
        ("failure_reason", None),
        ("failure_reason", "server_start_failed"),
        ("child_exit_code", 0),
        ("child_exit_code", 2),
        ("child_exit_code", 70),
        ("child_exit_code", 130),
    ],
)
def test_validate_payload_rejects_normal_playwright_failure_contract_mutations(
    tmp_path: Path, field: str, value: str | int | None
) -> None:
    # Given a normal Playwright failure with one contract field forged.
    payload = _manifest(tmp_path)
    payload.update(
        {
            "status": "failed",
            "failure_reason": "playwright_failed",
            "child_exit_code": 1,
        }
    )
    payload[field] = value

    # When the receipt is independently checked.
    with pytest.raises(ManifestValidationError):
        validate_payload(payload, repository_root=tmp_path)

    # Then wrong status, reason, and exit semantics cannot masquerade as normal failure.


@pytest.mark.parametrize(
    ("selected", "executed"),
    [
        ([], []),
        (
            ["scripted-smoke::e2e/smoke.spec.ts::works"],
            ["scripted-smoke::e2e/smoke.spec.ts::other"],
        ),
    ],
)
def test_validate_payload_rejects_normal_playwright_execution_mismatch(
    tmp_path: Path, selected: list[str], executed: list[str]
) -> None:
    # Given a normal Playwright failure with empty or mismatched execution.
    payload = _manifest(tmp_path)
    payload.update(
        {
            "status": "failed",
            "failure_reason": "playwright_failed",
            "child_exit_code": 1,
            "selected_ids": selected,
            "executed_ids": executed,
            "unexpected_failures": [
                {
                    "node_id": "scripted-smoke::e2e/smoke.spec.ts::works",
                    "status": "failed",
                }
            ],
        }
    )

    # When the receipt is independently checked.
    with pytest.raises(ManifestValidationError, match="node_execution_mismatch"):
        validate_payload(payload, repository_root=tmp_path)

    # Then a normal failure must prove a nonempty exact selected/executed set.


def test_validate_payload_rejects_execution_startup_failure_without_logs(tmp_path: Path) -> None:
    payload = _manifest(tmp_path)
    payload.update(
        {
            "status": "failed",
            "failure_reason": "playwright_failed",
            "child_exit_code": 1,
            "executed_ids": [],
            "unexpected_failures": [],
        }
    )

    with pytest.raises(ManifestValidationError, match="selection_artifacts"):
        validate_payload(payload, repository_root=tmp_path)


def test_main_dispatches_mixed_postgres_and_e2e_manifests(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given one manifest per supported runner, already parsed by the shared read boundary.
    script_path = Path(__file__).resolve().parents[2] / "scripts/check-isolation-cleanup.py"
    spec = importlib.util.spec_from_file_location("cleanup_cli", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    postgres = tmp_path / "postgres.json"
    e2e = tmp_path / "e2e.json"
    calls: list[str] = []
    payloads = {
        postgres: {"schema_version": 1, "mode": "all"},
        e2e: {"schema_version": 1, "runner": "moldy-isolated-e2e"},
    }
    monkeypatch.setattr(module, "load_manifest", lambda path: payloads[path])
    monkeypatch.setattr(module, "validate_payload", lambda _payload: calls.append("postgres"))
    monkeypatch.setattr(
        module, "validate_live_absence", lambda _payload: calls.append("postgres_live")
    )
    monkeypatch.setattr(
        module,
        "validate_e2e_payload",
        lambda _payload, **_kwargs: (calls.append("e2e"), "manifest-only")[1],
    )
    monkeypatch.setattr(
        module, "validate_e2e_live_absence", lambda _payload: calls.append("e2e_live")
    )
    monkeypatch.setattr(sys, "argv", ["check-isolation-cleanup.py", str(postgres), str(e2e)])

    # When the public checker receives both artifact types.
    exit_code = module.main()

    # Then each remains on its own checker without changing PostgreSQL validation.
    assert exit_code == 0
    assert calls == ["postgres", "postgres_live", "e2e", "e2e_live"]


def _configure_live_absence_probe(monkeypatch: pytest.MonkeyPatch, port: int) -> dict[str, object]:
    monkeypatch.setattr(lifecycle, "PORTS", {"scripted": (port,), "live": (port,)})
    monkeypatch.setattr(
        lifecycle,
        "probe_docker",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(args=(), returncode=1),
    )
    return {"lane": "scripted", "run_id": "a" * 24}


def test_validate_live_absence_rejects_actual_loopback_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given the fixed port has a TCP listener that accepts connections.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        payload = _configure_live_absence_probe(monkeypatch, port)

        # When post-cleanup live residue is checked.
        with pytest.raises(ManifestValidationError, match="live_port"):
            lifecycle.validate_live_absence(payload)

    # Then accepting listeners fail the cleanup gate.


def test_validate_live_absence_accepts_bound_non_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given a locally bound but non-listening port, analogous to TIME_WAIT bind contention.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as bound_socket:
        bound_socket.bind(("127.0.0.1", 0))
        port = bound_socket.getsockname()[1]
        payload = _configure_live_absence_probe(monkeypatch, port)

        # When post-cleanup live residue is checked by bounded TCP connect.
        lifecycle.validate_live_absence(payload)

    # Then bind availability is not mistaken for an active E2E listener.
