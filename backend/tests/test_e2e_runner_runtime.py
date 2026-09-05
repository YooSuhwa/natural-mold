"""Run-root trust-boundary regressions for the isolated E2E runner."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import e2e_runner_runtime as runtime  # noqa: E402
from e2e_cleanup_export_paths import read_regular  # noqa: E402
from e2e_runner_runtime import ProvisioningError  # noqa: E402


def test_prepare_run_root_uses_physical_temp_parent_for_safe_receipt_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    physical_test_root = tmp_path.resolve(strict=True)
    real_parent = physical_test_root / "real-temp"
    real_parent.mkdir()
    linked_parent = physical_test_root / "linked-temp"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    expected_root = real_parent / f"{runtime.RUN_ROOT_PREFIX}regression"
    observed_preparation_paths: list[Path] = []

    monkeypatch.setattr(runtime.tempfile, "gettempdir", lambda: str(linked_parent))

    def fake_mkdtemp(**options: object) -> str:
        assert options == {"prefix": runtime.RUN_ROOT_PREFIX, "dir": real_parent}
        expected_root.mkdir(mode=0o755)
        return str(expected_root)

    monkeypatch.setattr(runtime.tempfile, "mkdtemp", fake_mkdtemp)

    def fake_run_command(command: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        prepared_path = Path(command[2])
        observed_preparation_paths.append(prepared_path)
        (prepared_path / "receipt.json").write_text('{"status":"ok"}\n', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "prepared\n", "")

    monkeypatch.setattr(runtime, "run_command", fake_run_command)

    prepared_root, root_stat = runtime._prepare_run_root()

    assert prepared_root == expected_root
    assert observed_preparation_paths == [expected_root]
    assert stat.S_IMODE(root_stat.st_mode) == stat.S_IRWXU
    assert (root_stat.st_dev, root_stat.st_ino) == (
        expected_root.stat().st_dev,
        expected_root.stat().st_ino,
    )
    assert read_regular(prepared_root / "receipt.json") == b'{"status":"ok"}\n'


def test_create_run_root_rejects_a_replaced_symlink_leaf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    temp_parent = tmp_path.resolve(strict=True) / "temp"
    temp_parent.mkdir()
    outside = tmp_path.resolve(strict=True) / "outside"
    outside.mkdir()
    replaced_leaf = temp_parent / f"{runtime.RUN_ROOT_PREFIX}replaced"
    replaced_leaf.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(runtime.tempfile, "gettempdir", lambda: str(temp_parent))
    monkeypatch.setattr(runtime.tempfile, "mkdtemp", lambda **_options: str(replaced_leaf))

    with pytest.raises(OSError, match="unsafe_run_root"):
        runtime._create_run_root()

    assert outside.is_dir()
    assert list(outside.iterdir()) == []


def test_prepare_run_root_rejects_identity_replacement_after_preparation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    temp_parent = tmp_path.resolve(strict=True) / "temp"
    temp_parent.mkdir()
    run_root = temp_parent / f"{runtime.RUN_ROOT_PREFIX}original"
    displaced_root = temp_parent / "displaced-root"
    cleanup_calls: list[tuple[Path, int, int]] = []

    monkeypatch.setattr(runtime.tempfile, "gettempdir", lambda: str(temp_parent))

    def fake_mkdtemp(**_options: object) -> str:
        run_root.mkdir()
        return str(run_root)

    def replace_during_preparation(
        command: list[str], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        run_root.rename(displaced_root)
        run_root.mkdir()
        return subprocess.CompletedProcess(command, 0, "prepared\n", "")

    def observe_cleanup(path: Path, device: int, inode: int) -> bool:
        cleanup_calls.append((path, device, inode))
        return False

    monkeypatch.setattr(runtime.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(runtime, "run_command", replace_during_preparation)
    monkeypatch.setattr(runtime, "cleanup_run_root", observe_cleanup)

    with pytest.raises(ProvisioningError) as caught:
        runtime._prepare_run_root()

    assert caught.value.reason == "RuntimeError"
    assert caught.value.cleanup["cleanup_run_root_removed"] is False
    assert len(cleanup_calls) == 1
    assert cleanup_calls[0][0] == run_root
    assert (cleanup_calls[0][1], cleanup_calls[0][2]) == (
        displaced_root.stat().st_dev,
        displaced_root.stat().st_ino,
    )
