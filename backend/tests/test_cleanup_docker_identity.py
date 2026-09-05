"""Docker executable identity binding at the cleanup probe boundary."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from tests.project_gate_wave_support import load_module

type RunValue = bool | float | str | tuple[str, ...] | dict[str, str] | None


def _write_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)


def _forbid_subprocess(
    *args: str | tuple[str, ...], **kwargs: RunValue
) -> subprocess.CompletedProcess[str]:
    del args, kwargs
    pytest.fail("Docker subprocess ran after executable identity rejection")


def test_probe_rejects_docker_replaced_after_project_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a preflight token, when Docker is replaced, then no probe process starts."""
    docker_trust = load_module("cleanup_docker")
    docker = tmp_path / "trusted-system" / "docker"
    replacement = tmp_path / "replacement"
    _write_executable(docker)
    identity = docker_trust.capture_docker_identity(docker)
    token = docker_trust.docker_identity_token(identity)
    _write_executable(replacement)
    replacement.replace(docker)
    monkeypatch.setattr(docker_trust, "_trusted_system_paths", lambda: (str(docker.parent),))
    monkeypatch.setenv(docker_trust.DOCKER_ENVIRONMENT_NAME, str(docker))
    monkeypatch.setenv(docker_trust.DOCKER_IDENTITY_ENVIRONMENT_NAME, token)
    monkeypatch.setattr(docker_trust.subprocess, "run", _forbid_subprocess)

    with pytest.raises(docker_trust.DockerTrustError, match=r"^docker_identity_changed$"):
        docker_trust.probe_docker(("ps", "-aq"), timeout=10)


def test_probe_rechecks_identity_after_environment_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a late replacement, when the environment is built, then execution is denied."""
    docker_trust = load_module("cleanup_docker")
    docker = tmp_path / "trusted-system" / "docker"
    replacement = tmp_path / "replacement"
    _write_executable(docker)
    identity = docker_trust.capture_docker_identity(docker)
    _write_executable(replacement)
    path_checks = 0

    def trusted_paths() -> tuple[str, ...]:
        nonlocal path_checks
        path_checks += 1
        if path_checks == 2:
            replacement.replace(docker)
        return (str(docker.parent),)

    monkeypatch.setattr(docker_trust, "_trusted_system_paths", trusted_paths)
    monkeypatch.setenv(docker_trust.DOCKER_ENVIRONMENT_NAME, str(docker))
    monkeypatch.setenv(
        docker_trust.DOCKER_IDENTITY_ENVIRONMENT_NAME,
        docker_trust.docker_identity_token(identity),
    )
    monkeypatch.setattr(docker_trust.subprocess, "run", _forbid_subprocess)

    with pytest.raises(docker_trust.DockerTrustError, match=r"^docker_identity_changed$"):
        docker_trust.probe_docker(("ps", "-aq"), timeout=10)

    assert path_checks == 2


def test_probe_accepts_matching_preflight_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given an unchanged preflight identity, when probed, then Docker may run."""
    docker_trust = load_module("cleanup_docker")
    docker = tmp_path / "trusted-system" / "docker"
    _write_executable(docker)
    identity = docker_trust.capture_docker_identity(docker)
    token = docker_trust.docker_identity_token(identity)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(docker_trust, "_trusted_system_paths", lambda: (str(docker.parent),))
    monkeypatch.setenv(docker_trust.DOCKER_ENVIRONMENT_NAME, str(docker))
    monkeypatch.setenv(docker_trust.DOCKER_IDENTITY_ENVIRONMENT_NAME, token)

    def run(
        argv: tuple[str, ...],
        **kwargs: RunValue,
    ) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(docker_trust.subprocess, "run", run)

    result = docker_trust.probe_docker(("ps", "-aq"), timeout=10)

    assert result.returncode == 0
    assert calls == [(str(docker), "ps", "-aq")]


def test_probe_rejects_docker_symlink_retargeted_after_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a captured symlink, when retargeted, then the probe cannot execute."""
    docker_trust = load_module("cleanup_docker")
    prefix = tmp_path / "trusted-system"
    first = tmp_path / "docker-first"
    second = tmp_path / "docker-second"
    docker = prefix / "docker"
    replacement = prefix / "docker-replacement"
    _write_executable(first)
    _write_executable(second)
    prefix.mkdir(mode=0o700)
    docker.symlink_to(first)
    identity = docker_trust.capture_docker_identity(docker)
    replacement.symlink_to(second)
    replacement.replace(docker)
    monkeypatch.setattr(docker_trust, "_trusted_system_paths", lambda: (str(prefix),))
    monkeypatch.setenv(docker_trust.DOCKER_ENVIRONMENT_NAME, str(docker))
    monkeypatch.setenv(
        docker_trust.DOCKER_IDENTITY_ENVIRONMENT_NAME,
        docker_trust.docker_identity_token(identity),
    )
    monkeypatch.setattr(docker_trust.subprocess, "run", _forbid_subprocess)

    with pytest.raises(docker_trust.DockerTrustError, match=r"^docker_identity_changed$"):
        docker_trust.probe_docker(("ps", "-aq"), timeout=10)


def test_probe_rejects_same_inode_mutation_after_preflight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a captured file, when its bytes change in place, then the token no longer matches."""
    docker_trust = load_module("cleanup_docker")
    docker = tmp_path / "trusted-system" / "docker"
    _write_executable(docker)
    identity = docker_trust.capture_docker_identity(docker)
    inode = docker.stat().st_ino
    docker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    assert docker.stat().st_ino == inode
    monkeypatch.setattr(docker_trust, "_trusted_system_paths", lambda: (str(docker.parent),))
    monkeypatch.setenv(docker_trust.DOCKER_ENVIRONMENT_NAME, str(docker))
    monkeypatch.setenv(
        docker_trust.DOCKER_IDENTITY_ENVIRONMENT_NAME,
        docker_trust.docker_identity_token(identity),
    )
    monkeypatch.setattr(docker_trust.subprocess, "run", _forbid_subprocess)

    with pytest.raises(docker_trust.DockerTrustError, match=r"^docker_identity_changed$"):
        docker_trust.probe_docker(("ps", "-aq"), timeout=10)


def test_probe_rejects_same_inode_equal_length_rewrite_with_original_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a same-size rewrite with restored mtime, when probed, then no process starts."""
    docker_trust = load_module("cleanup_docker")
    docker = tmp_path / "trusted-system" / "docker"
    _write_executable(docker)
    identity = docker_trust.capture_docker_identity(docker)
    original = docker.stat()
    inode = original.st_ino
    docker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    os.utime(docker, ns=(original.st_atime_ns, original.st_mtime_ns))
    rewritten = docker.stat()
    assert rewritten.st_ino == inode
    assert rewritten.st_size == original.st_size
    assert rewritten.st_mtime_ns == original.st_mtime_ns
    rewritten_identity = docker_trust.capture_docker_identity(docker)
    assert rewritten_identity.target_sha256 != identity.target_sha256
    identity_token = docker_trust.docker_identity_token(identity)
    assert docker_trust.docker_identity_token(rewritten_identity) != identity_token
    monkeypatch.setattr(docker_trust, "_trusted_system_paths", lambda: (str(docker.parent),))
    monkeypatch.setenv(docker_trust.DOCKER_ENVIRONMENT_NAME, str(docker))
    monkeypatch.setenv(
        docker_trust.DOCKER_IDENTITY_ENVIRONMENT_NAME,
        identity_token,
    )
    monkeypatch.setattr(docker_trust.subprocess, "run", _forbid_subprocess)

    with pytest.raises(docker_trust.DockerTrustError, match=r"^docker_identity_changed$"):
        docker_trust.probe_docker(("ps", "-aq"), timeout=10)


def test_capture_rejects_oversized_docker_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given an oversized executable, when captured, then the trust boundary fails closed."""
    docker_trust = load_module("cleanup_docker")
    docker = tmp_path / "trusted-system" / "docker"
    _write_executable(docker)
    monkeypatch.setattr(docker_trust, "MAX_DOCKER_EXECUTABLE_BYTES", docker.stat().st_size - 1)

    with pytest.raises(docker_trust.DockerTrustError, match=r"^docker_preflight_failed$"):
        docker_trust.capture_docker_identity(docker)


def test_capture_rejects_unreadable_docker_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given an unreadable executable, when captured, then the trust boundary fails closed."""
    docker_trust = load_module("cleanup_docker")
    docker = tmp_path / "trusted-system" / "docker"
    _write_executable(docker)

    def unreadable(_descriptor: int, _size: int) -> bytes:
        raise OSError("read failed")

    monkeypatch.setattr(docker_trust.os, "read", unreadable)

    with pytest.raises(docker_trust.DockerTrustError, match=r"^docker_preflight_failed$"):
        docker_trust.capture_docker_identity(docker)


@pytest.mark.parametrize("token", ["not-a-digest", "f" * 64])
def test_probe_rejects_invalid_or_mismatched_identity_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    """Given an untrusted identity token, when probed, then fail before execution."""
    docker_trust = load_module("cleanup_docker")
    docker = tmp_path / "trusted-system" / "docker"
    _write_executable(docker)
    monkeypatch.setattr(docker_trust, "_trusted_system_paths", lambda: (str(docker.parent),))
    monkeypatch.setenv(docker_trust.DOCKER_ENVIRONMENT_NAME, str(docker))
    monkeypatch.setenv(docker_trust.DOCKER_IDENTITY_ENVIRONMENT_NAME, token)
    monkeypatch.setattr(docker_trust.subprocess, "run", _forbid_subprocess)

    with pytest.raises(docker_trust.DockerTrustError, match=r"^docker_identity_changed$"):
        docker_trust.probe_docker(("ps", "-aq"), timeout=10)


def test_gate_environment_binds_preflight_docker_identity(
    tmp_path: Path,
) -> None:
    """Given a captured toolchain, when sanitized, then its Docker token wins."""
    docker_trust = load_module("cleanup_docker")
    process = load_module("project_gate_process")
    toolchain = load_module("project_gate_toolchain")
    docker = tmp_path / "trusted-system" / "docker"
    _write_executable(docker)
    identity = toolchain._capture_executable(docker)
    trusted = toolchain.TrustedToolchain(
        Path("/trusted/python"),
        Path("/trusted/node"),
        Path("/trusted/pnpm"),
        Path("/trusted/uv"),
        tmp_path,
        "tester",
        docker=docker,
        identities=(identity,),
    )

    environment = process.safe_environment(
        {
            "MOLDY_GATE_DOCKER": "/shadow/docker",
            "MOLDY_GATE_DOCKER_IDENTITY": "f" * 64,
        },
        trusted,
    )

    assert environment["MOLDY_GATE_DOCKER"] == str(docker)
    assert identity.target_sha256 == hashlib.sha256(docker.read_bytes()).hexdigest()
    assert identity.target_ctime_ns == docker.stat().st_ctime_ns
    assert environment["MOLDY_GATE_DOCKER_IDENTITY"] == docker_trust.docker_identity_token(identity)


def test_gate_environment_rejects_missing_docker_identity(tmp_path: Path) -> None:
    """Given only a Docker path, when sanitized, then preflight fails closed."""
    process = load_module("project_gate_process")
    toolchain = load_module("project_gate_toolchain")
    trusted = toolchain.TrustedToolchain(
        Path("/trusted/python"),
        Path("/trusted/node"),
        Path("/trusted/pnpm"),
        Path("/trusted/uv"),
        tmp_path,
        "tester",
    )

    with pytest.raises(process.ProjectGateError, match=r"^runtime_preflight_failed$"):
        process.safe_environment({}, trusted)
