"""Identity-bound root and manifest cleanup behaviors."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "run-isolated-command.sh"
CLEANUP_HELPER = REPO_ROOT / "scripts" / "cleanup-isolated-root.py"


def _load_cleanup_helper() -> ModuleType:
    spec = importlib.util.spec_from_file_location("isolated_cleanup_security", CLEANUP_HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cleanup_reports_root_recreated_after_expected_inode_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the owned root is valid and the original basename is recreated after quarantine.
    helper = _load_cleanup_helper()
    root = tmp_path / ".moldy-test-run.fixture"
    root.mkdir()
    expected = helper.read_identity(root)
    original_rmdir = helper.os.rmdir

    def recreate_after_remove(name: str, *, dir_fd: int | None = None) -> None:
        original_rmdir(name, dir_fd=dir_fd)
        if name.startswith(helper.QUARANTINE_PREFIX):
            os.mkdir(root.name, dir_fd=dir_fd)

    monkeypatch.setattr(helper.os, "rmdir", recreate_after_remove)

    # When: cleanup removes the expected quarantined inode.
    result = helper.cleanup_owned_root(root, expected)
    monkeypatch.setattr(helper.os, "rmdir", original_rmdir)

    # Then: it reports the recreated basename rather than claiming removal.
    try:
        assert result == "root_recreated"
        assert root.is_dir()
    finally:
        if root.is_dir():
            shutil.rmtree(root)


@pytest.mark.parametrize("kind", ["existing", "symlink"])
def test_manifest_unsafe_initial_path_fails_before_child_and_preserves_bytes(
    tmp_path: Path, kind: str
) -> None:
    # Given: the explicit manifest path already exists or is a symlink.
    manifest = tmp_path / "manifest.json"
    target = tmp_path / "target.json"
    target.write_text("preserve-me")
    if kind == "existing":
        manifest.write_text("preserve-me")
    else:
        manifest.symlink_to(target)
    marker = tmp_path / "child-ran"

    # When: the wrapper validates the manifest before child launch.
    result = subprocess.run(
        [
            "/bin/bash",
            str(RUNNER),
            "--cwd",
            "backend",
            "--",
            sys.executable,
            "-c",
            f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran')",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "MOLDY_CLEANUP_MANIFEST": str(manifest)},
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: child never runs and neither existing object is changed.
    assert result.returncode == 74
    assert not marker.exists()
    assert target.read_text() == "preserve-me"
    if kind == "existing":
        assert manifest.read_text() == "preserve-me"
    else:
        assert manifest.is_symlink()


def test_manifest_replacement_during_child_is_preserved_and_finalize_fails(tmp_path: Path) -> None:
    # Given: the wrapper will securely create a new manifest before the child starts.
    manifest = tmp_path / "manifest.json"
    replacement = "replacement-must-survive"
    code = (
        f"import pathlib; path=pathlib.Path({str(manifest)!r}); "
        f"path.unlink(); path.write_text({replacement!r})"
    )

    # When: the child replaces the manifest pathname before finalization.
    result = subprocess.run(
        ["/bin/bash", str(RUNNER), "--cwd", "backend", "--", sys.executable, "-c", code],
        cwd=REPO_ROOT,
        env={**os.environ, "MOLDY_CLEANUP_MANIFEST": str(manifest)},
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: finalization fails without touching the replacement.
    assert result.returncode == 74
    assert manifest.read_text() == replacement
    assert "removed" not in result.stdout


def test_manifest_success_is_private_regular_json(tmp_path: Path) -> None:
    # Given: a fresh trusted absolute manifest path.
    manifest = tmp_path / "manifest.json"

    # When: a successful child completes.
    result = subprocess.run(
        ["/bin/bash", str(RUNNER), "--cwd", "backend", "--", "true"],
        cwd=REPO_ROOT,
        env={**os.environ, "MOLDY_CLEANUP_MANIFEST": str(manifest)},
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: the bound file contains the final receipt with private permissions.
    payload = json.loads(manifest.read_text())
    assert result.returncode == 0
    assert payload["cleanup"] == "removed"
    assert manifest.stat().st_mode & 0o777 == 0o600


def test_manifest_untrusted_parent_fails_before_child(tmp_path: Path) -> None:
    # Given: an absolute manifest parent writable by other local users.
    parent = tmp_path / "untrusted"
    parent.mkdir()
    parent.chmod(0o777)
    manifest = parent / "manifest.json"
    marker = tmp_path / "child-ran"

    # When: the wrapper validates the destination before child launch.
    result = subprocess.run(
        [
            "/bin/bash",
            str(RUNNER),
            "--cwd",
            "backend",
            "--",
            sys.executable,
            "-c",
            f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran')",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "MOLDY_CLEANUP_MANIFEST": str(manifest)},
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: no file is created and the child is never executed.
    assert result.returncode == 74
    assert not manifest.exists()
    assert not marker.exists()
