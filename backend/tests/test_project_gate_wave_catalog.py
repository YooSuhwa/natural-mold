"""Configuration and catalog contracts for canonical project-gate waves."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from tests.project_gate_wave_support import (
    EXPECTED_WAVE_1,
    EXPECTED_WAVE_2,
    SCRIPTS,
    config_path,
    load_module,
)


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    return load_module("project_gate_runner")


def test_config_loads_exact_canonical_wave_order(runner: ModuleType) -> None:
    """Given tracked config, when loaded, then both waves retain canonical node order."""
    profiles = runner.load_profiles(SCRIPTS / "project-gates.json")

    assert profiles["wave-1"].nodes == EXPECTED_WAVE_1
    assert profiles["wave-2"].nodes == EXPECTED_WAVE_2
    assert profiles["wave-1"].nodes != profiles["wave-2"].nodes


@pytest.mark.parametrize("mutation", ["unknown", "duplicate", "reordered", "omitted", "argv"])
def test_config_rejects_noncanonical_composite_catalog(
    tmp_path: Path, runner: ModuleType, mutation: str
) -> None:
    """Given unreviewed composite data, when parsed, then it fails before execution."""
    nodes: list[str] = list(EXPECTED_WAVE_1)
    if mutation == "unknown":
        nodes[-1] = "shell-command"
    elif mutation == "duplicate":
        nodes[-1] = nodes[0]
    elif mutation == "reordered":
        nodes[0], nodes[1] = nodes[1], nodes[0]
    elif mutation == "omitted":
        nodes.pop(3)
    else:
        path = config_path(tmp_path, nodes)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["profiles"]["wave-1"]["argv"] = ["sh", "-c", "touch pwned"]
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(runner.ProjectGateError, match="invalid_config"):
            runner.load_profiles(path)
        return

    with pytest.raises(runner.ProjectGateError, match="invalid_config"):
        runner.load_profiles(config_path(tmp_path, nodes))


def test_config_rejects_future_wave_without_review(tmp_path: Path, runner: ModuleType) -> None:
    """Given an undeclared future wave, when parsed, then the closed profile set rejects it."""
    path = config_path(tmp_path, list(EXPECTED_WAVE_1))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["profiles"]["wave-3"] = {
        "kind": "composite",
        "nodes": list(EXPECTED_WAVE_2),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(runner.ProjectGateError, match="invalid_config"):
        runner.load_profiles(path)
