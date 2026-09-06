"""Runtime and aggregate-writer integrity contracts for project-gate waves."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import ModuleType

import pytest

from tests.project_gate_wave_support import (
    allow_stable_repository,
    composite_args,
    load_module,
    receipt_summary,
)


@pytest.fixture(scope="module")
def composite() -> ModuleType:
    return load_module("project_gate_composite")


@pytest.fixture(scope="module")
def manifest_io() -> ModuleType:
    return load_module("project_gate_manifest")


@pytest.fixture(scope="module")
def toolchain() -> ModuleType:
    return load_module("project_gate_toolchain")


@pytest.mark.parametrize(
    ("python_version", "node_version"),
    [("Python 3.11.9", "v22.1.0"), ("Python 3.12.9", "v24.1.0")],
)
def test_runtime_preflight_rejects_wrong_python_or_node_major(
    tmp_path: Path,
    toolchain: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    python_version: str,
    node_version: str,
) -> None:
    """Given an unpinned runtime, when preflight probes it, then the gate fails closed."""
    python = tmp_path / "backend/.venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    python.chmod(0o700)
    selected = iter((Path("/trusted/node"), Path("/trusted/pnpm"), Path("/trusted/uv")))
    monkeypatch.setattr(toolchain, "_first_executable", lambda _candidates: next(selected))
    monkeypatch.setattr(
        toolchain.pwd,
        "getpwuid",
        lambda _uid: type("Account", (), {"pw_dir": str(tmp_path), "pw_name": "tester"})(),
    )
    monkeypatch.setattr(
        toolchain,
        "_version",
        lambda executable, _root, **_kwargs: {
            str(python): python_version,
            "/trusted/node": node_version,
            "/trusted/pnpm": "10.0.0",
        }[str(executable)],
    )

    with pytest.raises(toolchain.ProjectGateError, match="runtime_preflight_failed"):
        toolchain.resolve_toolchain(tmp_path)


def test_aggregate_writer_rejects_existing_and_symlink_paths(
    tmp_path: Path, manifest_io: ModuleType
) -> None:
    """Given an occupied or linked final path, when reserved, then overwrite/follow is refused."""
    existing = tmp_path / "existing.json"
    existing.write_text("{}", encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(existing)

    for path in (existing, linked):
        with pytest.raises(manifest_io.ProjectGateError):
            manifest_io.AggregateWriter.create(path)


def test_aggregate_writer_detects_link_replacement(tmp_path: Path, manifest_io: ModuleType) -> None:
    """Given a replaced aggregate name, when finalized, then pinned identity rejects it."""
    path = tmp_path / "wave.json"
    writer = manifest_io.AggregateWriter.create(path)
    path.unlink()
    path.write_text("{}", encoding="utf-8")
    try:
        with pytest.raises(manifest_io.ProjectGateError, match="unsafe_manifest"):
            writer.write({"status": "failed"})
    finally:
        writer.close()


def test_aggregate_writer_preserves_attacker_swap_after_parent_fsync(
    tmp_path: Path, manifest_io: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject a post-fsync swap while preserving the attacker's replacement."""
    path = tmp_path / "wave.json"
    writer = manifest_io.AggregateWriter.create(path)
    real_fsync = manifest_io.os.fsync
    swapped = False

    def swap_after_parent_fsync(descriptor: int) -> None:
        nonlocal swapped
        real_fsync(descriptor)
        if descriptor == writer.parent_descriptor and not swapped:
            swapped = True
            attacker = tmp_path / "attacker.json"
            attacker.write_text("attacker-owned", encoding="utf-8")
            attacker.replace(path)

    monkeypatch.setattr(manifest_io.os, "fsync", swap_after_parent_fsync)
    try:
        with pytest.raises(manifest_io.ProjectGateError, match="manifest_write_failed"):
            writer.write({"status": "passed"})
    finally:
        writer.close()

    assert path.read_text(encoding="utf-8") == "attacker-owned"


def test_aggregate_writer_writes_private_bound_regular_file(
    tmp_path: Path, manifest_io: ModuleType
) -> None:
    """Given an untouched destination, when finalized, then the named aggregate remains safe."""
    path = tmp_path / "wave.json"
    writer = manifest_io.AggregateWriter.create(path)
    try:
        writer.write({"status": "passed"})
    finally:
        writer.close()

    metadata = path.lstat()
    assert path.read_text(encoding="utf-8") == '{"status":"passed"}\n'
    assert stat.S_ISREG(metadata.st_mode)
    assert metadata.st_uid == os.geteuid()
    assert metadata.st_nlink == 1
    assert not metadata.st_mode & 0o022


def test_aggregate_file_is_private_regular_file(
    tmp_path: Path, composite: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a completed wave, when evidence is written, then it is a private regular file."""
    allow_stable_repository(composite, monkeypatch)
    monkeypatch.setattr(composite, "run_process", lambda _cmd, _root, _env: 0)
    monkeypatch.setattr(
        composite,
        "validate_child",
        lambda _node, path, _root, _exit, _env: receipt_summary(path),
    )
    manifest = tmp_path / "wave.json"

    composite.run_composite(
        "wave-1",
        ("backend-full",),
        *composite_args(composite, tmp_path),
        manifest,
        tmp_path,
        {},
    )

    metadata = manifest.lstat()
    assert stat.S_ISREG(metadata.st_mode)
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert metadata.st_nlink == 1
