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
        lambda path: scope if path == manifest else "",
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
