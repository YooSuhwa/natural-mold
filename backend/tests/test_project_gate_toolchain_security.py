"""Executable identity contracts for the composite project gate."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.project_gate_wave_support import load_module


def test_trusted_path_excludes_mutable_runtime_directories(tmp_path: Path) -> None:
    """Given narrow runtime bins, when validated, then only exact paths and versions survive."""
    toolchain = load_module("project_gate_toolchain")
    node_bin = tmp_path / ".nvm/versions/node/v22/bin"
    pnpm_bin = tmp_path / "Cellar/pnpm/10/bin"
    trusted = toolchain.TrustedToolchain(
        tmp_path / "backend/.venv/bin/python",
        node_bin / "node",
        pnpm_bin / "pnpm",
        tmp_path / "uv",
        tmp_path,
        "tester",
        runtime_paths=(str(node_bin), str(pnpm_bin)),
    )

    assert trusted.path == (f"{node_bin}:{pnpm_bin}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin")
    assert str(tmp_path / ".nvm/versions/node/v22") not in trusted.path.split(":")
    launcher = tmp_path / "bin/pnpm"
    launcher.parent.mkdir()
    launcher.symlink_to(Path("../Cellar/pnpm/10/bin/pnpm"))
    assert toolchain._one_hop_executable(launcher) == pnpm_bin / "pnpm"
    valid: tuple[tuple[str, re.Pattern[str], str], ...] = (
        ("Python 3.12.13", toolchain.PYTHON_VERSION, "3.12.13"),
        ("v22.22.3", toolchain.NODE_VERSION, "22.22.3"),
        ("10.32.1", toolchain.PNPM_VERSION, "10.32.1"),
    )
    for output, pattern, expected in valid:
        assert toolchain._normalized_version(output, pattern) == expected

    for output, pattern, _expected in valid:
        contaminated = f"{output} SECRET_TOKEN=leak"
        with pytest.raises(toolchain.ProjectGateError) as caught:
            toolchain._normalized_version(contaminated, pattern)
        assert "SECRET_TOKEN" not in str(caught.value)


def test_toolchain_identity_rejects_executable_replacement(tmp_path: Path) -> None:
    """Given captured runtime identities, when a path changes, then verification fails closed."""
    toolchain = load_module("project_gate_toolchain")
    runtime_bin = tmp_path / "node-bin"
    runtime_bin.mkdir(mode=0o700)
    node = runtime_bin / "node"
    node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    node.chmod(0o700)
    directory = toolchain._capture_runtime_directory(
        runtime_bin,
        required="node",
        allowed=frozenset({"corepack", "node", "npm", "npx"}),
    )
    identity = toolchain._capture_executable(node)
    trusted = toolchain.TrustedToolchain(
        node,
        node,
        node,
        node,
        tmp_path,
        "tester",
        runtime_paths=(str(runtime_bin),),
        identities=(identity,),
        directory_identities=(directory,),
    )
    toolchain.verify_toolchain(trusted)

    (runtime_bin / "evil").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    with pytest.raises(toolchain.ProjectGateError, match="runtime_changed"):
        toolchain.verify_toolchain(trusted)

    (runtime_bin / "evil").unlink()
    node.unlink()
    node.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    node.chmod(0o700)

    with pytest.raises(toolchain.ProjectGateError, match="runtime_changed"):
        toolchain.verify_toolchain(trusted)


def test_toolchain_identity_rejects_uv_replacement(tmp_path: Path) -> None:
    """Given a reviewed uv executable, when it is replaced, then verification fails closed."""
    toolchain = load_module("project_gate_toolchain")
    runtime_bin = tmp_path / "runtime-bin"
    runtime_bin.mkdir(mode=0o700)
    uv = runtime_bin / "uv"
    uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    uv.chmod(0o700)
    identity = toolchain._capture_executable(uv, require_regular=True)
    trusted = toolchain.TrustedToolchain(
        Path("/trusted/python"),
        Path("/trusted/node"),
        Path("/trusted/pnpm"),
        uv,
        tmp_path,
        "tester",
        identities=(identity,),
    )

    toolchain.verify_toolchain(trusted)

    uv.unlink()
    uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    uv.chmod(0o700)

    with pytest.raises(toolchain.ProjectGateError, match="runtime_changed"):
        toolchain.verify_toolchain(trusted)


def test_uv_identity_accepts_regular_and_one_hop_target(tmp_path: Path) -> None:
    """Given a regular uv target, when selected through one symlink, then capture succeeds."""
    toolchain = load_module("project_gate_toolchain")
    runtime_bin = tmp_path / "runtime-bin"
    runtime_bin.mkdir(mode=0o700)
    target = runtime_bin / "uv-real"
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o700)
    launcher = runtime_bin / "uv"
    launcher.symlink_to(target)

    selected = toolchain._one_hop_executable(launcher)
    identity = toolchain._capture_executable(selected, require_regular=True)

    assert selected == target
    assert not selected.is_symlink()
    assert identity.require_regular is True


def test_uv_identity_rejects_second_symlink_hop(tmp_path: Path) -> None:
    """Given a two-hop uv chain, when the first target is captured, then it is rejected."""
    toolchain = load_module("project_gate_toolchain")
    runtime_bin = tmp_path / "runtime-bin"
    runtime_bin.mkdir(mode=0o700)
    real_uv = runtime_bin / "uv-real"
    real_uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    real_uv.chmod(0o700)
    hop2 = runtime_bin / "uv-hop2"
    hop2.symlink_to(real_uv)
    hop1 = runtime_bin / "uv-hop1"
    hop1.symlink_to(hop2)

    selected = toolchain._one_hop_executable(hop1)

    assert selected == hop2
    with pytest.raises(toolchain.ProjectGateError, match="runtime_preflight_failed"):
        toolchain._capture_executable(selected, require_regular=True)
