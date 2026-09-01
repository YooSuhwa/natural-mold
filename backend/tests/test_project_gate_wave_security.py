"""Adversarial process, provenance, and writer contracts for composite gates."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from types import FrameType, ModuleType
from typing import Unpack

import pytest

from tests.project_gate_wave_support import (
    REPO_ROOT,
    SCRIPTS,
    RunKwargs,
    TrustedToolchainLike,
    load_module,
)


def _trusted(toolchain: ModuleType, tmp_path: Path) -> TrustedToolchainLike:
    return toolchain.TrustedToolchain(
        Path("/trusted/python"),
        Path("/trusted/node22/bin/node"),
        Path("/trusted/pnpm/bin/pnpm"),
        tmp_path,
        "tester",
    )


def test_catalog_commands_ignore_path_shadowing(tmp_path: Path) -> None:
    """Given shadow node/uv/pnpm, when commands build, then only trusted absolute tools appear."""
    catalog = load_module("project_gate_catalog")
    process = load_module("project_gate_process")
    toolchain = load_module("project_gate_toolchain")
    trusted = _trusted(toolchain, tmp_path)
    receipt = tmp_path / "receipt.json"

    backend, _ = process.command_for(catalog.CATALOG["backend-full"], receipt, REPO_ROOT, trusted)
    frontend, _ = process.command_for(
        catalog.CATALOG["frontend-build"], receipt, REPO_ROOT, trusted
    )
    capture, _ = process.command_for(
        catalog.CATALOG["todo14-visual-capture"], receipt, REPO_ROOT, trusted
    )

    assert backend[-6:] == [
        "/trusted/python",
        "-m",
        "pytest",
        "-q",
        "-m",
        "not integration",
    ]
    assert "uv" not in backend
    assert frontend[-3:] == [
        "/trusted/node22/bin/node",
        "/trusted/pnpm/bin/pnpm",
        "build",
    ]
    assert capture[:2] == ["/trusted/node22/bin/node", "/trusted/pnpm/bin/pnpm"]
    assert capture[-2:] == [
        "--project=scripted-capture",
        "e2e/chat-langgraph-v3-visual-matrix.spec.ts",
    ]


def test_trusted_git_ignores_shadow_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a PATH-shadow git, when provenance resolves, then only /usr/bin/git is invoked."""
    toolchain = load_module("project_gate_toolchain")
    shadow = tmp_path / "git"
    marker = tmp_path / "shadow-ran"
    shadow.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    shadow.chmod(0o700)
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], **_kwargs: Unpack[RunKwargs]
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:3] == ["rev-parse", "--verify"]:
            is_base = "^{commit}" in command[-1] and command[-1] != "HEAD^{commit}"
            output = "a" * 40 if is_base else "b" * 40
        else:
            output = ""
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=output + ("\n" if output else ""),
            stderr="",
        )

    monkeypatch.setattr(toolchain.subprocess, "run", fake_run)
    monkeypatch.setenv("PATH", str(tmp_path))

    provenance = toolchain.resolve_provenance("a" * 40, tmp_path)

    assert provenance.head_sha == "b" * 40
    assert all(command[0] == "/usr/bin/git" for command in calls)
    assert not marker.exists()


def test_isolated_wrapper_ignores_shadow_utilities(tmp_path: Path) -> None:
    """Given shadow utilities, when the wrapper runs, then only fixed tools are invoked."""
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    marker = tmp_path / "shadow-ran"
    for name in ("awk", "dirname", "git", "mktemp", "python3", "shasum", "sleep"):
        executable = shadow / name
        executable.write_text(
            f"#!/bin/sh\n/usr/bin/touch {str(marker)!r}\nexit 99\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
    manifest = tmp_path / "cleanup.json"
    environment = {
        "PATH": f"{shadow}:/usr/bin:/bin:/usr/sbin:/sbin",
        "MOLDY_CLEANUP_MANIFEST": str(manifest),
        "MOLDY_GATE_PYTHON": sys.executable,
        "TMPDIR": str(tmp_path),
    }

    completed = subprocess.run(
        [
            "/bin/bash",
            str(SCRIPTS / "run-isolated-command.sh"),
            "--cwd",
            "backend",
            "--",
            "/usr/bin/true",
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert manifest.is_file()
    assert not marker.exists()


def test_safe_environment_uses_canonical_system_tmpdir_for_no_follow_cleanup(
    tmp_path: Path,
) -> None:
    """Given the gate environment, when TMPDIR is selected, then it is physical and absolute."""
    process = load_module("project_gate_process")
    toolchain = load_module("project_gate_toolchain")
    trusted = _trusted(toolchain, tmp_path)

    environment = process.safe_environment({}, trusted)
    tmpdir = Path(environment["TMPDIR"])

    assert tmpdir == Path("/tmp").resolve(strict=True)
    assert tmpdir.is_absolute()
    assert tmpdir == tmpdir.resolve(strict=True)


def test_provenance_rejects_dirty_or_changed_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given dirty bytes or a changed HEAD, when rechecked, then the wave fails closed."""
    toolchain = load_module("project_gate_toolchain")
    expected = toolchain.RepositoryProvenance("a" * 40, "b" * 40)

    for head, status in (("b" * 40, "?? injected.py\n"), ("c" * 40, "")):
        results = iter(
            (
                subprocess.CompletedProcess([], 0, stdout=f"{head}\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout=status, stderr=""),
            )
        )
        monkeypatch.setattr(
            toolchain,
            "_run",
            lambda _command, _root, queue=results: next(queue),
        )
        with pytest.raises(toolchain.ProjectGateError, match="repository_changed"):
            toolchain.verify_provenance(expected, tmp_path)


def test_term_ignoring_process_group_is_killed_and_reaped(tmp_path: Path) -> None:
    """Given a TERM-ignoring child, when caller is terminated, then bounded KILL reaps it."""
    process = load_module("project_gate_process")
    pid_path = tmp_path / "child.pid"
    script = (
        "import os,signal,time,pathlib;"
        f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()));"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);time.sleep(60)"
    )
    previous = signal.getsignal(signal.SIGTERM)

    def raise_signal(number: int, _frame: FrameType | None) -> None:
        raise process.GateSignal(number)

    signal.signal(signal.SIGTERM, raise_signal)
    first_timer = threading.Timer(0.25, lambda: os.kill(os.getpid(), signal.SIGTERM))
    second_timer = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM))
    started = time.monotonic()
    first_timer.start()
    second_timer.start()
    try:
        with pytest.raises(process.GateSignal):
            process.run_process([sys.executable, "-c", script], tmp_path, {"PATH": "/usr/bin:/bin"})
    finally:
        first_timer.join()
        second_timer.join()
        signal.signal(signal.SIGTERM, previous)
    elapsed = time.monotonic() - started
    child_pid = int(pid_path.read_text(encoding="utf-8"))

    assert elapsed < 5
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_term_ignoring_descendant_is_killed_after_parent_exits(tmp_path: Path) -> None:
    """Given a TERM-default parent, when its child ignores TERM, then the group is reaped."""
    process = load_module("project_gate_process")
    pid_path = tmp_path / "descendant.pid"
    descendant = (
        "import os,signal,time,pathlib;"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
        f"pathlib.Path({str(pid_path)!r}).write_text(str(os.getpid()));"
        "time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{descendant!r}]);"
        "time.sleep(60)"
    )
    previous = signal.getsignal(signal.SIGTERM)

    def raise_signal(number: int, _frame: FrameType | None) -> None:
        raise process.GateSignal(number)

    signal.signal(signal.SIGTERM, raise_signal)
    timer = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM))
    timer.start()
    descendant_pid: int | None = None
    try:
        with pytest.raises(process.GateSignal):
            process.run_process(
                [sys.executable, "-c", parent],
                tmp_path,
                {"PATH": "/usr/bin:/bin"},
            )
        descendant_pid = int(pid_path.read_text(encoding="utf-8"))
        with pytest.raises(ProcessLookupError):
            os.kill(descendant_pid, 0)
    finally:
        timer.join()
        signal.signal(signal.SIGTERM, previous)
        if descendant_pid is not None:
            with suppress(ProcessLookupError):
                os.kill(descendant_pid, signal.SIGKILL)


def test_writer_unlinks_partial_file_after_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a partial write failure, when finalizing, then no partial aggregate remains."""
    manifest_io = load_module("project_gate_manifest")
    path = tmp_path / "wave.json"
    writer = manifest_io.AggregateWriter.create(path)
    real_write = manifest_io.os.write
    calls = 0

    def fail_after_partial(descriptor: int, data: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, data[: max(1, len(data) // 2)])
        raise OSError("injected write failure")

    monkeypatch.setattr(manifest_io.os, "write", fail_after_partial)
    try:
        with pytest.raises(manifest_io.ProjectGateError, match="manifest_write_failed"):
            writer.write({"status": "failed"})
    finally:
        writer.close()

    assert not path.exists()
