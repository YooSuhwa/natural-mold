"""Project propagation contract for isolated E2E resource provisioning."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import e2e_test_runner as runner  # noqa: E402
from e2e_runner_contract import Lane, Project  # noqa: E402
from e2e_runner_runtime import ProvisioningError  # noqa: E402


def test_scripted_capture_project_reaches_resource_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given a capture project, when the runner starts, then provisioning receives it."""
    observed: list[tuple[Lane, Project]] = []
    monkeypatch.setattr(runner, "assert_node22", lambda: None)
    monkeypatch.setattr(runner, "assert_ports_available", lambda _ports: None)

    def observe_project(lane: Lane, project: Project) -> None:
        observed.append((lane, project))
        raise ProvisioningError(
            "stop_after_project_observation",
            runner.empty_cleanup(),
            runner.initial_ownership(),
        )

    monkeypatch.setattr(runner, "provision_resources", observe_project)

    manifest, exit_code = runner._run("scripted", "scripted-capture", ())

    assert exit_code == 1
    assert manifest["failure_reason"] == "stop_after_project_observation"
    assert observed == [("scripted", "scripted-capture")]
