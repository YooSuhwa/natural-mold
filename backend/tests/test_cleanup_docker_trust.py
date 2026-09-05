"""Trusted Docker executable contracts shared by cleanup validators."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.cleanup_discovery_support import (
    absent_probes_fixture as _absent_probes_fixture,  # noqa: F401
)
from tests.cleanup_discovery_support import discovery
from tests.project_gate_wave_support import load_module, synthetic_docker_identity


def _write_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)


def test_cleanup_docker_resolution_ignores_inherited_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: inherited PATH begins with an executable Docker shadow.
    docker_trust = load_module("cleanup_docker")
    shadow = tmp_path / "shadow" / "docker"
    trusted = tmp_path / "trusted-system" / "docker"
    _write_executable(shadow)
    _write_executable(trusted)
    monkeypatch.setenv("PATH", str(shadow.parent))
    monkeypatch.delenv(docker_trust.DOCKER_ENVIRONMENT_NAME, raising=False)
    monkeypatch.setattr(docker_trust, "_trusted_system_paths", lambda: (str(trusted.parent),))

    # When: the cleanup boundary resolves Docker.
    selected = docker_trust.resolve_trusted_docker()

    # Then: only the explicitly reviewed system search path participates.
    assert selected == str(trusted)


def test_cleanup_docker_resolution_rejects_unsafe_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: the only fixed-prefix Docker candidate is group/world writable.
    docker_trust = load_module("cleanup_docker")
    docker = tmp_path / "trusted-system" / "docker"
    _write_executable(docker)
    docker.chmod(0o722)
    monkeypatch.setattr(docker_trust, "_trusted_system_paths", lambda: (str(docker.parent),))

    # When/Then: executable identity validation fails closed.
    with pytest.raises(docker_trust.DockerTrustError, match=r"^docker_preflight_failed$"):
        docker_trust.resolve_trusted_docker()


def test_cleanup_docker_resolution_rejects_requested_path_outside_system_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a caller requests an executable outside the reviewed system directories.
    docker_trust = load_module("cleanup_docker")
    trusted = tmp_path / "trusted-system" / "docker"
    shadow = tmp_path / "shadow" / "docker"
    _write_executable(trusted)
    _write_executable(shadow)
    monkeypatch.setattr(docker_trust, "_trusted_system_paths", lambda: (str(trusted.parent),))
    monkeypatch.setenv("MOLDY_GATE_DOCKER", str(shadow))

    # When/Then: an inherited executable override cannot expand the trust boundary.
    with pytest.raises(docker_trust.DockerTrustError, match=r"^docker_preflight_failed$"):
        docker_trust.resolve_trusted_docker()


def test_cleanup_docker_probe_drops_endpoint_selectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the caller tries to redirect a trusted Docker CLI to a clean daemon.
    docker_trust = load_module("cleanup_docker")
    trusted = "/usr/local/bin/docker"
    trusted_path = ("/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin")
    captured: list[tuple[tuple[str, ...], dict[str, str]]] = []
    monkeypatch.setenv("PATH", "/shadow")
    monkeypatch.setenv("DOCKER_HOST", "tcp://127.0.0.1:65535")
    monkeypatch.setenv("DOCKER_CONTEXT", "empty-context")
    monkeypatch.setenv("DOCKER_CONFIG", "/attacker/config")
    monkeypatch.setattr(docker_trust, "resolve_trusted_docker", lambda: trusted)
    monkeypatch.setattr(docker_trust, "_trusted_system_paths", lambda: trusted_path)

    def run(
        argv: tuple[str, ...],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        check: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, timeout, check
        captured.append((argv, env))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(docker_trust.subprocess, "run", run)

    # When: an absence probe is launched.
    docker_trust.probe_docker(("ps", "-aq"), timeout=10)

    # Then: argv is absolute and the child receives only endpoint-neutral variables.
    assert captured == [
        (
            (trusted, "ps", "-aq"),
            {"HOME": "/", "LC_ALL": "C", "PATH": ":".join(trusted_path)},
        )
    ]


def test_discovery_probe_uses_shared_trusted_docker(
    tmp_path: Path, absent_probes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: discovery runs with an inherited Docker shadow.
    root = tmp_path / "evidence"
    root.mkdir(mode=0o700)
    shadow = tmp_path / "shadow" / "docker"
    _write_executable(shadow)
    calls: list[tuple[str, ...]] = []
    monkeypatch.setenv("PATH", str(shadow.parent))

    def probe(arguments: tuple[str, ...], *, timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(discovery, "probe_docker", probe)

    # When: the global ownership probe executes.
    discovery.discover_and_probe(root)

    # Then: its argv starts with the reviewed absolute executable, never PATH lookup.
    assert calls[0][:2] == ("ps", "-aq")


def test_postgres_manifest_probe_uses_shared_trusted_docker_and_fixed_ps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a positional PostgreSQL manifest requires live-resource probes.
    checker = load_module("postgres_cleanup_checker")
    docker_calls: list[tuple[str, ...]] = []
    process_calls: list[list[str]] = []

    def docker_probe(
        arguments: tuple[str, ...], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        del timeout
        docker_calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 1, "", "")

    def probe(
        argv: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, timeout, check
        process_calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(checker, "probe_docker", docker_probe)
    monkeypatch.setattr(checker.subprocess, "run", probe)
    payload = {
        "scenarios": [
            {
                "container_id": "a" * 64,
                "run_root_created": False,
                "process_id": 999_999,
                "process_identity_sha256": "b" * 64,
            }
        ]
    }

    # When: live absence is checked.
    checker.validate_live_absence(payload)

    # Then: Docker is shared-trusted and ps is a fixed absolute system command.
    assert docker_calls == [("inspect", "a" * 64)]
    assert process_calls[0][0] == "/bin/ps"


def test_e2e_manifest_probe_uses_shared_trusted_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an E2E manifest requires a logical container probe.
    lifecycle = load_module("e2e_cleanup_lifecycle")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(lifecycle, "PORTS", {"scripted": ()})

    def probe(arguments: tuple[str, ...], *, timeout: float) -> subprocess.CompletedProcess[str]:
        del timeout
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 1, "", "")

    monkeypatch.setattr(lifecycle, "probe_docker", probe)

    # When: live absence is checked.
    lifecycle.validate_live_absence({"lane": "scripted", "run_id": "a" * 24})

    # Then: the container lookup uses the same reviewed absolute executable.
    assert calls == [("container", "inspect", f"moldy-e2e-{'a' * 24}")]


def test_gate_environment_overwrites_inherited_docker_executable(tmp_path: Path) -> None:
    # Given: a hostile caller tries to replace the preflight-captured Docker executable.
    process = load_module("project_gate_process")
    toolchain = load_module("project_gate_toolchain")
    trusted = toolchain.TrustedToolchain(
        Path("/trusted/python"),
        Path("/trusted/node"),
        Path("/trusted/pnpm"),
        Path("/trusted/uv"),
        tmp_path,
        "tester",
        docker=Path("/usr/local/bin/docker"),
        identities=(synthetic_docker_identity(),),
    )

    # When: the composite child environment is sanitized.
    environment = process.safe_environment(
        {
            "MOLDY_GATE_DOCKER": "/shadow/docker",
            "MOLDY_GATE_DOCKER_IDENTITY": "f" * 64,
        },
        trusted,
    )

    # Then: only the captured path reaches the cleanup validators.
    assert environment["MOLDY_GATE_DOCKER"] == "/usr/local/bin/docker"
    assert environment["MOLDY_GATE_DOCKER_IDENTITY"] != "f" * 64
