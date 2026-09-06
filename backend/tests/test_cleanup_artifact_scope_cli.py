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
