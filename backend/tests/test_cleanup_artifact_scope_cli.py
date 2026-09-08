"""CI artifact-scope output for the independent cleanup validator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))


def _cleanup_cli() -> ModuleType:
    script = SCRIPTS_ROOT / "check-isolation-cleanup.py"
    spec = importlib.util.spec_from_file_location("cleanup_artifact_scope_cli", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("scope", ["manifest-only", "full"])
def test_cli_prints_the_single_validated_artifact_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    scope: str,
) -> None:
    module = _cleanup_cli()
    manifest = tmp_path / "receipt.json"
    monkeypatch.setattr(
        module,
        "_validate_manifest",
        lambda path: module.ManifestValidationResult(scope, None) if path == manifest else None,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check-isolation-cleanup.py",
            "--print-artifact-scope",
            str(manifest),
        ],
    )

    assert module.main() == 0
    assert capsys.readouterr().out == f"{scope}\n"


def test_cli_prints_validated_full_artifact_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _cleanup_cli()
    manifest = tmp_path / "receipt.json"
    directory = "output/e2e-captures/20260906-pr-smoke"
    monkeypatch.setattr(
        module,
        "_validate_manifest",
        lambda path: (
            module.ManifestValidationResult("full", directory) if path == manifest else None
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check-isolation-cleanup.py",
            "--print-artifact-metadata",
            str(manifest),
        ],
    )

    assert module.main() == 0
    assert capsys.readouterr().out == (f"artifact_scope=full\nartifact_directory={directory}\n")


def test_cli_reports_only_the_stable_manifest_rejection_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _cleanup_cli()
    manifest = tmp_path / "receipt.json"

    def reject(_path: Path) -> None:
        raise module.ManifestValidationError("self_test")

    monkeypatch.setattr(module, "_validate_manifest", reject)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check-isolation-cleanup.py",
            "--print-artifact-metadata",
            str(manifest),
        ],
    )

    assert module.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "manifest validation rejected: self_test\n"


def test_e2e_failure_diagnostic_never_reflects_receipt_values() -> None:
    module = _cleanup_cli()
    cleanup = dict.fromkeys(module.CLEANUP_FIELDS, True)
    cleanup["foreign_containers_preserved"] = True
    payload = {
        "runner": "moldy-isolated-e2e",
        "status": "failed",
        "self_test": "normal",
        "failure_reason": "password=must-not-appear",
        "child_exit_code": 70,
        "owned_run_root": True,
        "owned_database": True,
        "owned_backend": False,
        "owned_frontend": False,
        "owned_proxy": False,
        "selected_ids": ["token=must-not-appear"],
        "executed_ids": [],
        "export": {
            "secret_scan_passed": True,
            "failure_code": "password=must-not-appear",
            "source_rejection": {"untrusted": "must-not-appear"},
            "files": [{"path": "results/selection.json"}],
        },
        "cleanup": cleanup,
    }

    diagnostic = module._e2e_failure_diagnostic(payload)

    assert diagnostic == (
        "status=failed self_test=normal phase=other exit=70 ownership=11000 "
        "selected=present executed=empty export=passed export_failure=invalid "
        "source_rejected=yes "
        "receipts=missing cleanup=complete failures=invalid failure_phases=invalid "
        "network_failure_codes=invalid location=invalid"
    )
    assert "password" not in diagnostic
    assert "token" not in diagnostic
    assert "must-not-appear" not in diagnostic


def test_postgres_failure_diagnostic_projects_only_bounded_states() -> None:
    module = _cleanup_cli()
    payload = {
        "schema_version": 1,
        "mode": "all",
        "status": "failed",
        "scenarios": [
            {
                "scenario": "all",
                "status": "failed",
                "failure_reason": "child_exit",
                "child_exit_code": 1,
                "container_id": "secret-container-id",
                "test_receipt": {
                    "selected_node_ids": ["tests/secret_test.py::test_secret"],
                    "executed_node_ids": ["tests/secret_test.py::test_secret"],
                    "failed_node_ids": ["tests/secret_test.py::test_secret"],
                },
                "cleanup_container_removed": True,
                "owned_label_absent": True,
                "port_mapping_removed": True,
                "process_group_stopped": True,
                "cleanup_run_root_removed": True,
                "foreign_containers_observed": False,
                "foreign_containers_preserved": None,
            }
        ],
    }

    diagnostic = module._postgres_failure_diagnostic(payload)

    assert diagnostic == (
        "status=failed mode=all scenario=all reason=child_exit exit=1 "
        "selected=present executed=present failed=present cleanup=complete"
    )
    assert "secret" not in diagnostic


def test_postgres_failure_diagnostic_never_reflects_unknown_values() -> None:
    module = _cleanup_cli()
    payload = {
        "schema_version": 1,
        "mode": "password=must-not-appear",
        "status": "token=must-not-appear",
        "scenarios": [
            {
                "scenario": "cookie=must-not-appear",
                "failure_reason": "authorization=must-not-appear",
                "child_exit_code": "secret-exit",
            }
        ],
    }

    diagnostic = module._postgres_failure_diagnostic(payload)

    assert diagnostic == (
        "status=other mode=other scenario=other reason=other exit=other "
        "selected=invalid executed=invalid failed=invalid cleanup=incomplete"
    )
    assert "must-not-appear" not in diagnostic
    assert "secret-exit" not in diagnostic


def test_e2e_failure_diagnostic_projects_only_safe_failure_metadata() -> None:
    module = _cleanup_cli()
    payload = {
        "runner": "moldy-isolated-e2e",
        "status": "failed",
        "self_test": "normal",
        "project": "scripted-full",
        "failure_reason": "playwright_failed",
        "child_exit_code": 1,
        "owned_run_root": True,
        "owned_database": True,
        "owned_backend": True,
        "owned_frontend": True,
        "owned_proxy": False,
        "selected_ids": [],
        "executed_ids": [],
        "unexpected_failures": [
            {
                "node_id": ("scripted-full::e2e/token-secretvalue.spec.ts::title-secretvalue"),
                "status": "failed",
                "location": {
                    "file": "e2e/token-secretvalue.spec.ts",
                    "line": 42,
                    "column": 7,
                },
                "network_failure_codes": [
                    "api_request_failure",
                    "other_response_failure",
                ],
                "failure_phase": "open_draft",
            }
        ],
    }

    diagnostic = module._e2e_failure_diagnostic(payload)

    assert diagnostic is not None
    assert "failures=1" in diagnostic
    assert "failure_phases=open_draft" in diagnostic
    assert "network_failure_codes=api_request_failure,other_response_failure" in diagnostic
    assert "location=one" in diagnostic
    for canary in ("token-secretvalue", "title-secretvalue", "42", "7"):
        assert canary not in diagnostic


@pytest.mark.parametrize(
    "unexpected_failures",
    [
        [{"node_id": "scripted-full::/etc/token.spec.ts::title", "status": "failed"}],
        [{"node_id": "scripted-full::e2e/../token.spec.ts::title", "status": "failed"}],
        [{"node_id": r"scripted-full::e2e\\token.spec.ts::title", "status": "failed"}],
        [
            {
                "node_id": "scripted-full::e2e/token.spec.ts::title",
                "status": "failed",
                "location": {"file": "e2e/token.spec.ts", "line": True, "column": 1},
            }
        ],
        [
            {
                "node_id": "scripted-full::e2e/token.spec.ts::title",
                "status": "failed",
                "location": {"file": "e2e/other.spec.ts", "line": 1, "column": 1},
            }
        ],
        [
            {
                "node_id": "scripted-full::e2e/token.spec.ts::title",
                "status": "failed",
                "failure_phase": "phase=token-secretvalue",
            }
        ],
        [
            {
                "node_id": "scripted-full::e2e/token.spec.ts::title",
                "status": "failed",
                "network_failure_codes": ["code=token-secretvalue"],
            }
        ],
        [
            {
                "node_id": "scripted-full::e2e/token.spec.ts::title",
                "status": "failed",
                "unexpected": "raw-error-token-secretvalue",
            }
        ],
        [
            {
                "node_id": "scripted-full::e2e/token.spec.ts::title",
                "status": "failed",
                "location": {"file": "e2e/token.spec.ts", "line": 0, "column": 1},
            }
        ],
        [
            {
                "node_id": "scripted-full::e2e/token.spec.ts::title",
                "status": "failed",
                "location": {
                    "file": "e2e/token.spec.ts",
                    "line": 1_000_001,
                    "column": 1,
                },
            }
        ],
        [
            {
                "node_id": "scripted-full::e2e/token-secretvalue.spec.ts::title",
                "status": "failed",
                "location": {
                    "file": "e2e/token-secretvalue.spec.ts\x00",
                    "line": 1,
                    "column": 1,
                },
            }
        ],
        [
            {
                "node_id": "scripted-full::e2e/token.spec.ts::title",
                "status": "failed",
            },
            {
                "node_id": "scripted-full::e2e/token.spec.ts::title",
                "status": "failed",
            },
        ],
        [
            *[
                {
                    "node_id": f"scripted-full::e2e/token-{index}.spec.ts::title",
                    "status": "failed",
                }
                for index in range(17)
            ]
        ],
    ],
)
def test_e2e_failure_diagnostic_rejects_malformed_failure_metadata(
    unexpected_failures: list[dict[str, object]],
) -> None:
    module = _cleanup_cli()
    payload = {
        "runner": "moldy-isolated-e2e",
        "status": "failed",
        "self_test": "normal",
        "project": "scripted-full",
        "unexpected_failures": unexpected_failures,
    }

    diagnostic = module._e2e_failure_diagnostic(payload)

    assert diagnostic is not None
    assert "failures=invalid" in diagnostic
    assert "failure_phases=invalid" in diagnostic
    assert "network_failure_codes=invalid" in diagnostic
    assert "location=invalid" in diagnostic
    assert "token-secretvalue" not in diagnostic
    assert "raw-error-token-secretvalue" not in diagnostic


@pytest.mark.parametrize(
    ("unexpected_failures", "expected_location"),
    [
        ([], "none"),
        (
            [
                {
                    "node_id": "scripted-full::e2e/token.spec.ts::title",
                    "status": "failed",
                }
            ],
            "none",
        ),
        (
            [
                {
                    "node_id": "scripted-full::e2e/token.spec.ts::title",
                    "status": "failed",
                    "location": {"file": "e2e/token.spec.ts", "line": 1, "column": 1},
                }
            ],
            "one",
        ),
        (
            [
                {
                    "node_id": f"scripted-full::e2e/token-{index}.spec.ts::title",
                    "status": "failed",
                    "location": {
                        "file": f"e2e/token-{index}.spec.ts",
                        "line": 1,
                        "column": 1,
                    },
                }
                for index in range(2)
            ],
            "multiple",
        ),
        (
            [
                {
                    "node_id": "scripted-full::e2e/token.spec.ts::title",
                    "status": "failed",
                    "location": {"file": "e2e/token.spec.ts", "line": 1, "column": 1},
                },
                {
                    "node_id": "scripted-full::e2e/other.spec.ts::title",
                    "status": "failed",
                },
            ],
            "one",
        ),
    ],
)
def test_e2e_failure_diagnostic_reports_location_cardinality(
    unexpected_failures: list[dict[str, object]],
    expected_location: str,
) -> None:
    module = _cleanup_cli()
    payload = {
        "runner": "moldy-isolated-e2e",
        "project": "scripted-full",
        "unexpected_failures": unexpected_failures,
    }

    diagnostic = module._e2e_failure_diagnostic(payload)

    assert diagnostic is not None
    assert f"location={expected_location}" in diagnostic


def test_e2e_failure_diagnostic_requires_a_known_project() -> None:
    module = _cleanup_cli()
    payload = {
        "runner": "moldy-isolated-e2e",
        "status": "failed",
        "self_test": "normal",
        "project": "project=token-secretvalue",
        "unexpected_failures": [],
    }

    diagnostic = module._e2e_failure_diagnostic(payload)

    assert diagnostic is not None
    assert "failures=invalid" in diagnostic
    assert "failure_phases=invalid" in diagnostic
    assert "network_failure_codes=invalid" in diagnostic
    assert "location=invalid" in diagnostic
    assert "token-secretvalue" not in diagnostic


@pytest.mark.parametrize(
    "failure_code",
    [
        "bounds",
        "export_adapter_exception",
        "exporter_process_failed",
        "internal",
        "invalid_export_receipt",
        "manifest_publish",
        "secret_scan",
        "source_topology",
        "unsupported_artifact",
    ],
)
def test_e2e_failure_diagnostic_reports_only_known_export_failure_codes(
    failure_code: str,
) -> None:
    module = _cleanup_cli()
    cleanup = dict.fromkeys(module.CLEANUP_FIELDS, True)
    cleanup["foreign_containers_preserved"] = True
    payload = {
        "runner": "moldy-isolated-e2e",
        "status": "failed",
        "self_test": "normal",
        "failure_reason": "artifact_export_failed",
        "child_exit_code": 1,
        "owned_run_root": True,
        "owned_database": True,
        "owned_backend": True,
        "owned_frontend": True,
        "owned_proxy": False,
        "selected_ids": ["scripted-smoke::e2e/smoke.spec.ts::works"],
        "executed_ids": [],
        "export": {
            "secret_scan_passed": False,
            "failure_code": failure_code,
            "source_rejection": None,
            "files": [],
        },
        "cleanup": cleanup,
    }

    diagnostic = module._e2e_failure_diagnostic(payload)

    assert diagnostic is not None
    assert f"export_failure={failure_code}" in diagnostic


def test_cli_prints_bounded_e2e_diagnostic_after_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _cleanup_cli()
    manifest = tmp_path / "receipt.json"
    diagnostic = "status=failed self_test=normal phase=selection"

    def reject(_path: Path) -> None:
        raise module.ManifestDiagnosticError("self_test", diagnostic)

    monkeypatch.setattr(module, "_validate_manifest", reject)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check-isolation-cleanup.py",
            "--print-artifact-metadata",
            str(manifest),
        ],
    )

    assert module.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"manifest validation rejected: self_test\ne2e diagnostic: {diagnostic}\n"
    )


def test_cli_prints_bounded_postgres_diagnostic_after_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _cleanup_cli()
    manifest = tmp_path / "receipt.json"
    diagnostic = "status=failed mode=all scenario=all reason=child_exit exit=1"

    def reject(_path: Path) -> None:
        raise module.PostgresManifestDiagnosticError("manifest_status", diagnostic)

    monkeypatch.setattr(module, "_validate_manifest", reject)
    monkeypatch.setattr(sys, "argv", ["check-isolation-cleanup.py", str(manifest)])

    assert module.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"manifest validation rejected: manifest_status\npostgres diagnostic: {diagnostic}\n"
    )


def test_cli_rejects_discovery_combined_with_manifest_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _cleanup_cli()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check-isolation-cleanup.py",
            "--discover",
            str(tmp_path),
            str(tmp_path / "receipt.json"),
        ],
    )

    with pytest.raises(SystemExit) as caught:
        module.main()

    assert caught.value.code == 2
