"""Adversarial process, provenance, and writer contracts for composite gates."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from types import FrameType, ModuleType
from typing import Unpack

import pytest

from tests.project_gate_wave_support import (
    REPO_ROOT,
    SCRIPTS,
    ReceiptSummaryPayload,
    RunKwargs,
    TrustedToolchainLike,
    load_module,
    receipt_summary,
    synthetic_docker_identity,
)


def _trusted(toolchain: ModuleType, tmp_path: Path) -> TrustedToolchainLike:
    return toolchain.TrustedToolchain(
        Path("/trusted/python"),
        Path("/trusted/node22/bin/node"),
        Path("/trusted/pnpm/bin/pnpm"),
        Path("/trusted/uv"),
        tmp_path,
        "tester",
        identities=(synthetic_docker_identity(),),
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
    todo21_capture, _ = process.command_for(
        catalog.CATALOG["todo21-visual-capture"], receipt, REPO_ROOT, trusted
    )
    todo21_runtime, _ = process.command_for(
        catalog.CATALOG["todo21-runtime-policy-e2e"], receipt, REPO_ROOT, trusted
    )
    migration, _ = process.command_for(
        catalog.CATALOG["postgres-migration-roundtrip"], receipt, REPO_ROOT, trusted
    )
    lifecycle, _ = process.command_for(
        catalog.CATALOG["postgres-run-lifecycle-stream-resume"], receipt, REPO_ROOT, trusted
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
    assert todo21_capture[-5:] == [
        "--project=scripted-capture",
        "e2e/agent-settings.spec.ts",
        "e2e/runtime-todo-policy.spec.ts",
        "e2e/runtime-filesystem-policy.spec.ts",
        "e2e/chat-compaction.spec.ts",
    ]
    assert todo21_runtime[-5:] == [
        "--project=scripted-full",
        "e2e/agent-settings.spec.ts",
        "e2e/runtime-todo-policy.spec.ts",
        "e2e/runtime-filesystem-policy.spec.ts",
        "e2e/chat-compaction.spec.ts",
    ]
    assert migration[-3:] == ["migration-roundtrip", "--manifest", str(receipt)]
    assert lifecycle[-4:] == ["run-lifecycle", "stream-resume", "--manifest", str(receipt)]


def test_composite_postgres_selector_binds_to_exact_internal_receipt_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a fixed public selector pair, when receipt validation runs, then its mode is exact."""
    catalog = load_module("project_gate_catalog")
    process = load_module("project_gate_process")
    captured: list[tuple[str, int]] = []

    def record_validation(
        _path: Path, _root: Path, mode: str, exit_code: int
    ) -> ReceiptSummaryPayload:
        captured.append((mode, exit_code))
        return receipt_summary(_path)

    monkeypatch.setattr(process, "_checker", lambda _receipt, _root, _env: True)
    monkeypatch.setattr(process, "validate_postgres", record_validation)

    process.validate_child(
        catalog.CATALOG["postgres-run-lifecycle-stream-resume"],
        tmp_path / "child.json",
        tmp_path,
        0,
        {},
    )

    assert captured == [("run-lifecycle+stream-resume", 0)]


@pytest.mark.parametrize(
    ("node_id", "expected_project", "expected_screenshot_count"),
    [
        ("todo21-runtime-policy-e2e", "scripted-full", None),
        ("todo21-visual-capture", "scripted-capture", 24),
    ],
)
def test_multi_spec_e2e_binds_to_exact_receipt_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    node_id: str,
    expected_project: str,
    expected_screenshot_count: int | None,
) -> None:
    """Given reviewed Wave 4 E2E nodes, when validated, then every spec and count binds."""
    catalog = load_module("project_gate_catalog")
    process = load_module("project_gate_process")
    captured: list[tuple[str | tuple[str, ...] | None, int | None]] = []

    def record_validation(
        _path: Path,
        _root: Path,
        _exit_code: int,
        *,
        project: str,
        expected_spec: str | tuple[str, ...] | None,
        expected_screenshot_count: int | None = None,
    ) -> ReceiptSummaryPayload:
        assert project == expected_project
        captured.append((expected_spec, expected_screenshot_count))
        return receipt_summary(_path)

    monkeypatch.setattr(process, "_checker", lambda _receipt, _root, _env: True)
    monkeypatch.setattr(process, "validate_e2e", record_validation)

    process.validate_child(
        catalog.CATALOG[node_id],
        tmp_path / "child.json",
        tmp_path,
        0,
        {},
    )

    assert captured == [
        (
            (
                "e2e/agent-settings.spec.ts",
                "e2e/runtime-todo-policy.spec.ts",
                "e2e/runtime-filesystem-policy.spec.ts",
                "e2e/chat-compaction.spec.ts",
            ),
            expected_screenshot_count,
        )
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


def _check_term_ignoring_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, descendant: bool
) -> None:
    process = load_module("project_gate_process")
    pid_path = tmp_path / "ignoring.pid"
    script = (
        "import os, signal, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pid_path = Path({str(pid_path)!r})\n"
        "temporary = pid_path.with_suffix('.tmp')\n"
        "temporary.write_text(str(os.getpid()))\n"
        "temporary.replace(pid_path)\n"
        "time.sleep(60)\n"
    )
    if descendant:
        script = (
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-I', '-S', '-c', {script!r}])\n"
            "time.sleep(60)\n"
        )

    child_group: list[subprocess.Popen[bytes]] = []
    forwarded: list[int] = []
    started = 0.0
    real_wait = subprocess.Popen.wait
    real_killpg = os.killpg

    def signal_ready_child(child: subprocess.Popen[bytes], timeout: float | None = None) -> int:
        nonlocal started
        if not child_group:
            child_group.append(child)
            deadline = time.monotonic() + 30
            while not pid_path.exists():
                assert child.poll() is None, "child exited before publishing its PID"
                assert time.monotonic() < deadline, "child did not publish its PID"
                time.sleep(0.01)
            # The gate is in its wait block and the ignoring process is ready.
            started = time.monotonic()
            os.kill(os.getpid(), signal.SIGTERM)
        return real_wait(child, timeout=timeout)

    def forward_signal(group: int, number: int) -> None:
        assert group == child_group[0].pid
        if number == signal.SIGKILL:
            if descendant:
                assert child_group[0].poll() == -signal.SIGTERM
            os.kill(int(pid_path.read_text(encoding="utf-8")), 0)
        real_killpg(group, number)
        if number:
            forwarded.append(number)
        if number == signal.SIGTERM:
            # Deliver the caller's second signal inside cleanup, after the group's TERM.
            os.kill(os.getpid(), signal.SIGTERM)

    def raise_signal(number: int, _frame: FrameType | None) -> None:
        raise process.GateSignal(number)

    monkeypatch.setattr(subprocess.Popen, "wait", signal_ready_child)
    monkeypatch.setattr(os, "killpg", forward_signal)
    previous = signal.signal(signal.SIGTERM, raise_signal)
    try:
        with pytest.raises(process.GateSignal):
            process.run_process(
                [sys.executable, "-I", "-S", "-c", script], tmp_path, {"PATH": "/usr/bin:/bin"}
            )
        assert forwarded == [signal.SIGTERM, signal.SIGKILL]
        assert time.monotonic() - started < 5
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_path.read_text(encoding="utf-8")), 0)
        with pytest.raises(ProcessLookupError):
            real_killpg(child_group[0].pid, 0)
    except BaseException:
        for child in child_group:
            with suppress(ProcessLookupError):
                real_killpg(child.pid, signal.SIGKILL)
            real_wait(child, timeout=5)
        raise
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_term_ignoring_process_group_is_killed_and_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _check_term_ignoring_group(tmp_path, monkeypatch, descendant=False)


def test_term_ignoring_descendant_is_killed_after_parent_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _check_term_ignoring_group(tmp_path, monkeypatch, descendant=True)


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
