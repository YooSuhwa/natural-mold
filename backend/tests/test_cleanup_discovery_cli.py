"""Public CLI and catalog tests for cleanup discovery."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.cleanup_discovery_support import CHECKER, discovery


def test_cleanup_cli_rejects_missing_mixed_and_multiple_discovery_roots(tmp_path: Path) -> None:
    # Given: the cleanup checker CLI and two candidate roots.
    roots = [tmp_path / "one", tmp_path / "two"]
    for root in roots:
        root.mkdir()

    # When: unsupported source combinations are invoked.
    invocations = [
        [],
        ["--discover", str(roots[0]), "manifest.json"],
        ["--discover", *map(str, roots)],
    ]
    results = [
        subprocess.run([sys.executable, str(CHECKER), *argv], check=False)  # noqa: S603
        for argv in invocations
    ]

    # Then: argparse rejects every ambiguous request.
    assert [result.returncode for result in results] == [2, 2, 2]


def test_cleanup_cli_reports_stable_discovery_failure(tmp_path: Path) -> None:
    # Given: malformed evidence whose absolute location must not enter diagnostics.
    root = tmp_path / "private-evidence"
    root.mkdir(mode=0o700)
    broken = root / "broken.json"
    broken.write_text("{", encoding="utf-8")
    broken.chmod(0o600)

    # When: discovery rejects the malformed evidence through the public CLI.
    result = subprocess.run(  # noqa: S603 - fixed local checker
        [sys.executable, str(CHECKER), "--discover", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: callers receive only the stable reason, without a traceback or path.
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "cleanup discovery rejected: discovery_json\n"
    assert str(root) not in result.stderr


def test_cleanup_cli_reports_stable_missing_root_failure(tmp_path: Path) -> None:
    # Given: a discovery root does not exist and its location is private.
    root = tmp_path / "missing-private-evidence"

    # When: the public CLI attempts bounded discovery.
    result = subprocess.run(  # noqa: S603 - fixed local checker
        [sys.executable, str(CHECKER), "--discover", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: raw filesystem details and traceback frames never leave the boundary.
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "cleanup discovery rejected: discovery_root\n"
    assert str(root) not in result.stderr


def test_canonical_wrapper_supplies_a_bound_active_root() -> None:
    # Given: the production isolation wrapper launches a direct Python child.
    runner = discovery.REPO_ROOT / "scripts" / "run-isolated-command.sh"
    child = (
        "import importlib.util,os,pathlib,sys;"
        f"p=pathlib.Path({str(CHECKER)!r});"
        "sys.path.insert(0,str(p.parent));"
        "s=importlib.util.spec_from_file_location('checker',p);"
        "assert s is not None and s.loader is not None;"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "assert m._active_from_environment()==os.environ['MOLDY_TEST_RUN_ROOT']"
    )
    environment = {**os.environ, "MOLDY_GATE_PYTHON": sys.executable}

    # When: the child validates the wrapper-bound active-root environment.
    result = subprocess.run(  # noqa: S603 - fixed repository wrapper and interpreter
        [
            "/bin/bash",
            str(runner),
            "--cwd",
            "backend",
            "--",
            sys.executable,
            "-c",
            child,
        ],
        cwd=discovery.REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: parent binding succeeds and the wrapper removes its disposable root.
    assert result.returncode == 0, result.stderr
    assert '"cleanup":"removed"' in result.stdout


def test_final_cleanup_catalog_uses_discovery_contract() -> None:
    # Given: the canonical final cleanup gate node.
    from project_gate_catalog import CATALOG

    # When: its immutable argv is inspected.
    argv = CATALOG["final-cleanup-discovery"].argv

    # Then: it invokes direct-root discovery rather than retrospective validation.
    assert argv == (
        "../scripts/check-isolation-cleanup.py",
        "--discover",
        "../.omo/evidence/project-restart-consolidated-roadmap",
    )
