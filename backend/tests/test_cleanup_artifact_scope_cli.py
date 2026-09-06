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
            "source_rejection": {"untrusted": "must-not-appear"},
            "files": [{"path": "results/selection.json"}],
        },
        "cleanup": cleanup,
    }

    diagnostic = module._e2e_failure_diagnostic(payload)

    assert diagnostic == (
        "status=failed self_test=normal phase=other exit=70 ownership=11000 "
        "selected=present executed=empty export=passed source_rejected=yes "
        "receipts=missing cleanup=complete"
    )
    assert "password" not in diagnostic
    assert "token" not in diagnostic
    assert "must-not-appear" not in diagnostic


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
