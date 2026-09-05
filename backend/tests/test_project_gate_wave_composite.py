"""Composite execution, signal, and environment contracts for project-gate waves."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.project_gate_wave_support import (
    GateNodeLike,
    ReceiptSummaryPayload,
    RepositoryProvenanceLike,
    allow_stable_repository,
    composite_args,
    load_module,
    receipt_summary,
)


@pytest.fixture(scope="module")
def composite() -> ModuleType:
    return load_module("project_gate_composite")


@pytest.fixture(scope="module")
def process() -> ModuleType:
    return load_module("project_gate_process")


def test_composite_success_aggregates_every_node(
    tmp_path: Path, composite: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given green fixed children, when a wave runs, then every expected node is recorded."""
    expected = ("backend-full", "frontend-lint")
    allow_stable_repository(composite, monkeypatch)
    monkeypatch.setattr(composite, "run_process", lambda _cmd, _root, _env: 0)
    monkeypatch.setattr(
        composite,
        "validate_child",
        lambda _node, path, _root, _exit, _env: receipt_summary(path),
    )
    manifest = tmp_path / "wave.json"

    result = composite.run_composite(
        "wave-1", expected, *composite_args(composite, tmp_path), manifest, tmp_path, {}
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert result == 0
    assert payload["status"] == "passed"
    assert payload["expected_node_ids"] == list(expected)
    assert payload["executed_node_ids"] == list(expected)
    assert payload["runtime"] == {"python": "3.12.9", "node": "22.1.0", "pnpm": "10.0.0"}
    assert [node["status"] for node in payload["nodes"]] == ["passed", "passed"]


def test_composite_failure_records_remaining_nodes_not_run(
    tmp_path: Path, composite: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a failed child, when fail-fast triggers, then terminal evidence remains complete."""
    expected = ("backend-full", "frontend-lint", "frontend-build")
    exits = iter((0, 23))
    allow_stable_repository(composite, monkeypatch)
    monkeypatch.setattr(composite, "run_process", lambda _cmd, _root, _env: next(exits))
    monkeypatch.setattr(
        composite,
        "validate_child",
        lambda _node, path, _root, _exit, _env: receipt_summary(path),
    )
    manifest = tmp_path / "wave.json"

    result = composite.run_composite(
        "wave-1", expected, *composite_args(composite, tmp_path), manifest, tmp_path, {}
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert result == 1
    assert payload["failure_reason"] == "child_failed"
    assert payload["executed_node_ids"] == list(expected[:2])
    assert [node["status"] for node in payload["nodes"]] == ["passed", "failed", "not_run"]


def test_composite_fails_when_repository_changes_after_node(
    tmp_path: Path, composite: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a mid-wave mutation, when a node ends, then later work is not started."""
    checks = 0

    def changed(_expected: RepositoryProvenanceLike, _root: Path) -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise composite.ProjectGateError("repository_changed")

    monkeypatch.setattr(composite, "verify_provenance", changed)
    monkeypatch.setattr(composite, "run_process", lambda _cmd, _root, _env: 0)
    monkeypatch.setattr(
        composite,
        "validate_child",
        lambda _node, path, _root, _exit, _env: receipt_summary(path),
    )
    manifest = tmp_path / "wave.json"

    result = composite.run_composite(
        "wave-1",
        ("backend-full", "frontend-lint"),
        *composite_args(composite, tmp_path),
        manifest,
        tmp_path,
        {},
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert result == 1
    assert checks == 2
    assert payload["failure_reason"] == "repository_changed"
    assert payload["executed_node_ids"] == ["backend-full"]


def test_composite_invalid_receipt_fails_closed(
    tmp_path: Path, composite: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a forged child receipt, when validation runs, then the wave cannot pass."""
    allow_stable_repository(composite, monkeypatch)
    monkeypatch.setattr(composite, "run_process", lambda _cmd, _root, _env: 0)

    def reject(
        _node: GateNodeLike,
        _path: Path,
        _root: Path,
        _exit: int,
        _environment: dict[str, str],
    ) -> None:
        raise composite.ProjectGateError("invalid_child_receipt")

    monkeypatch.setattr(composite, "validate_child", reject)
    manifest = tmp_path / "wave.json"

    result = composite.run_composite(
        "wave-1",
        ("backend-full",),
        *composite_args(composite, tmp_path),
        manifest,
        tmp_path,
        {},
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert result == 1
    assert payload["failure_reason"] == "invalid_child_receipt"
    assert payload["nodes"][0]["status"] == "invalid_receipt"


def test_signal_finalizes_manifest_and_validates_child_receipt(
    tmp_path: Path, composite: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given SIGTERM during a child, when reaped, then interrupted cleanup evidence is retained."""
    validated: list[int] = []
    allow_stable_repository(composite, monkeypatch)

    def interrupt(_command: list[str], _root: Path, _environment: dict[str, str]) -> int:
        raise composite.GateSignal(15)

    def validate(
        _node: GateNodeLike,
        path: Path,
        _root: Path,
        exit_code: int,
        _environment: dict[str, str],
    ) -> ReceiptSummaryPayload:
        validated.append(exit_code)
        return receipt_summary(path)

    monkeypatch.setattr(composite, "run_process", interrupt)
    monkeypatch.setattr(composite, "validate_child", validate)
    manifest = tmp_path / "wave.json"

    result = composite.run_composite(
        "wave-1",
        ("backend-full",),
        *composite_args(composite, tmp_path),
        manifest,
        tmp_path,
        {},
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert result == 143
    assert validated == [143]
    assert payload["failure_reason"] == "signal_15"
    assert payload["nodes"][0]["status"] == "interrupted"


def test_composite_environment_drops_secrets(tmp_path: Path, process: ModuleType) -> None:
    """Given credential-like environment values, when sanitized, then only launcher keys remain."""
    trusted = process.TrustedToolchain(
        Path("/trusted/python"),
        Path("/trusted/node22/bin/node"),
        Path("/trusted/pnpm/bin/pnpm"),
        Path("/trusted/uv"),
        tmp_path,
        "tester",
    )
    environment = process.safe_environment(
        {
            "PATH": "/bin",
            "HOME": "/tmp/home",
            "DOCKER_HOST": "unix:///tmp/docker.sock",
            "OPENAI_API_KEY": "secret",
            "NODE_OPTIONS": "--require=evil",
            "E2E_LLM_API_KEY": "secret",
            "MOLDY_GATE_PYTHON": "/untrusted/python",
            "MOLDY_GATE_UV": "/untrusted/uv",
        },
        trusted,
    )

    assert environment == {
        "PATH": trusted.path,
        "HOME": str(tmp_path),
        "TMPDIR": str(Path("/tmp").resolve(strict=True)),
        "USER": "tester",
        "LOGNAME": "tester",
        "LC_ALL": "C",
        "MOLDY_GATE_PYTHON": "/trusted/python",
        "MOLDY_GATE_UV": "/trusted/uv",
        "npm_config_manage_package_manager_versions": "false",
        "DOCKER_HOST": "unix:///tmp/docker.sock",
    }
