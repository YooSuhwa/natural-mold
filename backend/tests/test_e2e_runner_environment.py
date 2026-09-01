"""Child-environment boundary tests for the canonical E2E runner."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import e2e_runner_environment as environment_module  # noqa: E402
from e2e_runner_contract import E2eContractError, Project, build_e2e_dsns  # noqa: E402
from e2e_runner_environment import assert_node22, build_lane_environment  # noqa: E402


def _environment(tmp_path: Path, project: Project = "scripted-smoke") -> dict[str, str]:
    dsns = build_e2e_dsns(password="secret", port=54321, database="moldy_e2e_scripted_run123")
    return build_lane_environment("scripted", project, dsns, tmp_path)


@pytest.mark.parametrize("runtime", ["legacy", "langgraph_v3"])
def test_valid_chat_runtime_is_forwarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runtime: str
) -> None:
    monkeypatch.setenv("NEXT_PUBLIC_CHAT_RUNTIME", runtime)

    assert _environment(tmp_path)["NEXT_PUBLIC_CHAT_RUNTIME"] == runtime


def test_invalid_chat_runtime_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NEXT_PUBLIC_CHAT_RUNTIME", "experimental")

    with pytest.raises(E2eContractError, match="invalid_chat_runtime"):
        _environment(tmp_path)


def test_capture_tour_is_limited_to_scripted_capture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("E2E_CAPTURE_TOUR", "1")

    assert _environment(tmp_path, "scripted-capture")["E2E_CAPTURE_TOUR"] == "1"
    with pytest.raises(E2eContractError, match="invalid_capture_tour"):
        _environment(tmp_path, "scripted-smoke")


def test_capture_tour_rejects_noncanonical_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("E2E_CAPTURE_TOUR", "true")

    with pytest.raises(E2eContractError, match="invalid_capture_tour"):
        _environment(tmp_path, "scripted-capture")


def test_lane_environment_uses_only_the_fixed_runner_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Given hostile Python controls, when the child env is built, then the runner wins."""
    monkeypatch.setenv("MOLDY_GATE_PYTHON", "/untrusted/gate-python")
    monkeypatch.setenv("PYTHON", "/untrusted/python")

    environment = _environment(tmp_path)

    assert environment["MOLDY_GATE_PYTHON"] == sys.executable
    assert "PYTHON" not in environment


@pytest.mark.parametrize(
    "project",
    ["scripted-smoke", "scripted-full", "scripted-capture", "live-manual"],
)
def test_isolated_next_build_directory_uses_dot_next_component(
    tmp_path: Path, project: Project
) -> None:
    # Given: every supported isolated project.
    environment = _environment(tmp_path, project)

    # When: the runner derives its Next output directory.
    build_directory = Path(environment["E2E_NEXT_BUILD_DIR"])

    # Then: Next sees its standard ignored build component and never the watched legacy path.
    assert build_directory == tmp_path / "frontend" / ".next" / project
    assert build_directory.parent.name == ".next"
    assert tmp_path / "frontend" / "next" / project != build_directory


@pytest.mark.parametrize(("version", "accepted"), [("v22.22.3\n", True), ("v25.8.1\n", False)])
def test_node_major_guard_is_exact_and_preprovision(
    monkeypatch: pytest.MonkeyPatch, version: str, accepted: bool
) -> None:
    completed = environment_module.subprocess.CompletedProcess(["node"], 0, version, "")
    monkeypatch.setattr(environment_module.subprocess, "run", lambda *_args, **_kwargs: completed)

    if accepted:
        assert_node22()
    else:
        with pytest.raises(E2eContractError, match="node_major_mismatch"):
            assert_node22()
