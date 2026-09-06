"""Active-wrapper exclusion tests for cleanup discovery."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.cleanup_discovery_support import (
    ManifestValidationError,
    discovery,
)
from tests.cleanup_discovery_support import (
    absent_probes_fixture as _absent_probes_fixture,  # noqa: F401
)


def test_discovery_excludes_only_verified_active_wrapper_root(
    tmp_path: Path, absent_probes: Path
) -> None:
    # Given: the exact private owned active wrapper root under system temp.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    active = absent_probes / ".moldy-test-run.a1B2c3D4"
    active.mkdir(mode=0o700)

    # When: discovery receives the wrapper-owned root explicitly.
    summary = discovery.discover_and_probe(root, active_run_root=str(active))

    # Then: only that verified root is excluded.
    assert summary.receipts == ()


def test_discovery_rejects_same_name_active_root_replacement(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the verified active root is replaced after its first directory-entry stat.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    active = absent_probes / ".moldy-test-run.a1B2c3D4"
    active.mkdir(mode=0o700)
    original_stat = discovery.os.stat
    replaced = False

    def replace_after_stat(
        path: str | bytes | int,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal replaced
        metadata = original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if path == active.name and dir_fd is not None and not replaced:
            replaced = True
            active.rename(absent_probes / "old-active")
            active.mkdir(mode=0o700)
        return metadata

    monkeypatch.setattr(discovery.os, "stat", replace_after_stat)

    # When/Then: a stable name cannot hide a changed device/inode identity.
    with pytest.raises(ManifestValidationError, match=r"^probe_error$"):
        discovery.discover_and_probe(root, active_run_root=str(active))
    assert replaced is True


def test_discovery_rejects_forged_active_wrapper_exclusion(
    tmp_path: Path, absent_probes: Path
) -> None:
    # Given: an arbitrary directory lies outside the system temp root.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    forged = tmp_path / ".moldy-test-run.forged"
    forged.mkdir(mode=0o700)

    # When/Then: it cannot suppress global orphan detection.
    with pytest.raises(ManifestValidationError, match=r"^active_run_root$"):
        discovery.discover_and_probe(root, active_run_root=str(forged))


def test_discovery_rejects_unbound_active_wrapper_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: only the exclusion variable is forged without wrapper binding flags.
    monkeypatch.setenv("MOLDY_TEST_RUN_ROOT", "/tmp/.moldy-test-run.a1B2c3D4")
    monkeypatch.delenv("MOLDY_BACKEND_SOURCE_ROOT", raising=False)
    monkeypatch.delenv("MOLDY_DISABLE_ENV_FILE", raising=False)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)

    # When/Then: the CLI adapter refuses to honor the exclusion.
    with pytest.raises(ManifestValidationError, match=r"^active_run_root$"):
        discovery._active_from_environment()


def test_discovery_rejects_fully_forged_active_wrapper_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: public environment flags and an owned matching directory are forged directly.
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir(mode=0o700)
    candidate = system_temp / ".moldy-test-run.a1B2c3D4"
    candidate.mkdir(mode=0o700)
    monkeypatch.setattr(discovery, "SYSTEM_TEMP", system_temp)
    monkeypatch.setenv("MOLDY_TEST_RUN_ROOT", str(candidate))
    monkeypatch.setenv("MOLDY_BACKEND_SOURCE_ROOT", str(discovery.REPO_ROOT / "backend"))
    monkeypatch.setenv("MOLDY_DISABLE_ENV_FILE", "true")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setattr(
        discovery,
        "_run_probe",
        lambda argv, _timeout: subprocess.CompletedProcess(argv, 0, "/bin/zsh\n", ""),
    )

    # When/Then: flags alone cannot authorize a residue exclusion.
    with pytest.raises(ManifestValidationError, match=r"^active_run_root$"):
        discovery._active_from_environment()


def test_discovery_accepts_active_root_from_canonical_wrapper_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the exact canonical wrapper is the checker's parent process.
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir(mode=0o700)
    candidate = system_temp / ".moldy-test-run.a1B2c3D4"
    candidate.mkdir(mode=0o700)
    monkeypatch.setattr(discovery, "SYSTEM_TEMP", system_temp)
    monkeypatch.setenv("MOLDY_TEST_RUN_ROOT", str(candidate))
    monkeypatch.setenv("MOLDY_BACKEND_SOURCE_ROOT", str(discovery.REPO_ROOT / "backend"))
    monkeypatch.setenv("MOLDY_DISABLE_ENV_FILE", "true")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    parent_command = " ".join(
        (
            *discovery._WRAPPER_COMMAND_PREFIX,
            sys.executable,
            "../scripts/check-isolation-cleanup.py",
            "--discover",
            "../.omo/evidence/project-restart-consolidated-roadmap",
        )
    )
    monkeypatch.setattr(
        discovery,
        "_run_probe",
        lambda argv, _timeout: subprocess.CompletedProcess(argv, 0, parent_command, ""),
    )

    # When/Then: the checker may exclude only the wrapper-created current root.
    assert discovery._active_from_environment() == str(candidate)


@pytest.mark.parametrize("kind", ["pg-name", "writable", "symlink"])
def test_discovery_rejects_inside_temp_forged_active_root(
    tmp_path: Path, absent_probes: Path, kind: str
) -> None:
    # Given: an inside-temp path is not the exact wrapper-owned directory shape.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    candidate = absent_probes / (
        ".moldy-test-run.pg-a1B2c3D4" if kind == "pg-name" else ".moldy-test-run.a1B2c3D4"
    )
    if kind == "symlink":
        target = absent_probes / "target"
        target.mkdir(mode=0o700)
        candidate.symlink_to(target)
    else:
        candidate.mkdir(mode=0o700)
        if kind == "writable":
            candidate.chmod(0o770)

    # When/Then: forged exclusions fail closed.
    with pytest.raises(ManifestValidationError, match=r"^active_run_root$"):
        discovery.discover_and_probe(root, active_run_root=str(candidate))
