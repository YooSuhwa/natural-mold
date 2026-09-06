"""Process-group failure bookkeeping for the PostgreSQL runner."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT.parent / "scripts"))

import cleanup_docker  # noqa: E402
import postgres_runner_process  # noqa: E402


class _InterruptedProcess:
    pid = 424242
    returncode = -1

    def communicate(self, *, timeout: float) -> tuple[str, str]:
        del timeout
        raise KeyboardInterrupt


class _FinishedProcess:
    pid = 424243
    returncode = 0

    def communicate(self, *, timeout: float) -> tuple[str, str]:
        del timeout
        return "container-id\n", ""


def _write_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)


@pytest.mark.parametrize("stop_raises", [False, True])
def test_run_command_records_unkillable_group_after_base_exception(
    stop_raises: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given a child interrupted by BaseException whose process group remains alive.
    postgres_runner_process.start_process_scope()
    monkeypatch.setattr(
        postgres_runner_process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _InterruptedProcess(),
    )

    def stop_group(_process: _InterruptedProcess) -> None:
        if stop_raises:
            raise KeyboardInterrupt

    monkeypatch.setattr(postgres_runner_process, "_stop_process_group", stop_group)
    monkeypatch.setattr(postgres_runner_process, "_process_group_absent", lambda _group: False)

    try:
        # When the command boundary handles the interruption.
        with pytest.raises(KeyboardInterrupt):
            postgres_runner_process.run_command(["owned-child"])

        # Then active tracking is cleared but durable failure bookkeeping remains false-closed.
        assert postgres_runner_process.process_groups_stopped() is False
    finally:
        postgres_runner_process.start_process_scope()


def test_docker_command_uses_bound_executable_and_endpoint_neutral_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given hostile Docker selectors, when launched, then only the bound client may run."""
    docker = tmp_path / "trusted-system" / "docker"
    _write_executable(docker)
    identity = cleanup_docker.capture_docker_identity(docker)
    captured: list[tuple[list[str], dict[str, str] | None]] = []
    monkeypatch.setattr(cleanup_docker, "_trusted_system_paths", lambda: (str(docker.parent),))
    monkeypatch.setenv(cleanup_docker.DOCKER_ENVIRONMENT_NAME, str(docker))
    monkeypatch.setenv(
        cleanup_docker.DOCKER_IDENTITY_ENVIRONMENT_NAME,
        cleanup_docker.docker_identity_token(identity),
    )
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:65535")

    def popen(
        argv: list[str], *, env: dict[str, str] | None, **kwargs: bool | Path | int
    ) -> _FinishedProcess:
        del kwargs
        captured.append((argv, env))
        return _FinishedProcess()

    monkeypatch.setattr(postgres_runner_process.subprocess, "Popen", popen)
    monkeypatch.setattr(postgres_runner_process, "_process_group_absent", lambda _group: True)

    result = postgres_runner_process.run_command(
        ["docker", "ps", "-aq"],
        env={
            "POSTGRES_PASSWORD": "database-secret",
            "DOCKER_HOST": "tcp://127.0.0.1:65535",
            "DOCKER_CONTEXT": "attacker-context",
        },
    )

    assert result.args == ["docker", "ps", "-aq"]
    assert captured == [
        (
            [str(docker), "ps", "-aq"],
            {
                "HOME": "/",
                "LC_ALL": "C",
                "PATH": str(docker.parent),
                "POSTGRES_PASSWORD": "database-secret",
            },
        )
    ]


def test_docker_command_rechecks_identity_after_environment_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a late Docker swap, when spawning, then Popen is never reached."""
    docker = tmp_path / "trusted-system" / "docker"
    replacement = tmp_path / "replacement"
    _write_executable(docker)
    _write_executable(replacement)
    identity = cleanup_docker.capture_docker_identity(docker)
    monkeypatch.setattr(cleanup_docker, "_trusted_system_paths", lambda: (str(docker.parent),))
    monkeypatch.setenv(cleanup_docker.DOCKER_ENVIRONMENT_NAME, str(docker))
    monkeypatch.setenv(
        cleanup_docker.DOCKER_IDENTITY_ENVIRONMENT_NAME,
        cleanup_docker.docker_identity_token(identity),
    )

    def swap_environment(_environment: dict[str, str] | None) -> dict[str, str]:
        replacement.replace(docker)
        return {"HOME": "/", "LC_ALL": "C", "PATH": str(docker.parent)}

    def forbid_popen(*args: str | list[str], **kwargs: bool | Path | int) -> _FinishedProcess:
        del args, kwargs
        pytest.fail("Docker Popen ran after executable identity rejection")

    monkeypatch.setattr(
        postgres_runner_process,
        "docker_subprocess_environment",
        swap_environment,
        raising=False,
    )
    monkeypatch.setattr(postgres_runner_process.subprocess, "Popen", forbid_popen)

    with pytest.raises(cleanup_docker.DockerTrustError, match=r"^docker_identity_changed$"):
        postgres_runner_process.run_command(["docker", "ps", "-aq"])


def test_docker_command_rejects_equal_length_rewrite_with_restored_mtime_before_popen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a content rewrite after preflight, when mtime is restored, then Popen is denied."""
    docker = tmp_path / "trusted-system" / "docker"
    _write_executable(docker)
    identity = cleanup_docker.capture_docker_identity(docker)
    original = docker.stat()
    monkeypatch.setattr(cleanup_docker, "_trusted_system_paths", lambda: (str(docker.parent),))
    monkeypatch.setenv(cleanup_docker.DOCKER_ENVIRONMENT_NAME, str(docker))
    monkeypatch.setenv(
        cleanup_docker.DOCKER_IDENTITY_ENVIRONMENT_NAME,
        cleanup_docker.docker_identity_token(identity),
    )

    def rewrite_environment(_environment: dict[str, str] | None) -> dict[str, str]:
        docker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        os.utime(docker, ns=(original.st_atime_ns, original.st_mtime_ns))
        return {"HOME": "/", "LC_ALL": "C", "PATH": str(docker.parent)}

    def forbid_popen(*args: str | list[str], **kwargs: bool | Path | int) -> _FinishedProcess:
        del args, kwargs
        pytest.fail("Docker Popen ran after content identity rejection")

    monkeypatch.setattr(
        postgres_runner_process,
        "docker_subprocess_environment",
        rewrite_environment,
        raising=False,
    )
    monkeypatch.setattr(postgres_runner_process.subprocess, "Popen", forbid_popen)

    with pytest.raises(cleanup_docker.DockerTrustError, match=r"^docker_identity_changed$"):
        postgres_runner_process.run_command(["docker", "ps", "-aq"])
