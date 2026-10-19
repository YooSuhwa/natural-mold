"""The company-administration native lane runs a fixed, complete integration registry."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from postgres_company_suite import (  # noqa: E402
    COMPANY_ADMINISTRATION_LEGACY_NODES,
    COMPANY_ADMINISTRATION_MODULES,
    COMPANY_ADMINISTRATION_TARGETS,
    company_administration_argv,
    company_selection_gaps,
)
from postgres_runner_contract import build_lane_dsns  # noqa: E402
from postgres_runner_runtime import run_test_child  # noqa: E402

from tests.integration_marker_scan import integration_modules, is_integration_module  # noqa: E402

# Marker-detected integration modules at the B11/B12 planning baseline 6d5323ee.
BASELINE = Path(__file__).with_name("postgres_integration_baseline.txt")
BASELINE_SHA256 = "72619a4eed475c657037fae82497f85737b4584f2fa8896546e5a75e3af2602a"
_AMBIENT = (
    "DATABASE_URL",
    "DATABASE_URL_SYNC",
    "INTEGRATION_DATABASE_URL",
    "MOLDY_PG_TEST_RECEIPT",
)


def test_baseline_is_pinned() -> None:
    assert hashlib.sha256(BASELINE.read_bytes()).hexdigest() == BASELINE_SHA256


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        ("tests/authz/test_a.py", "import pytest\npytestmark = pytest.mark.integration\n", True),
        ("tests/authz/test_a.py", "pytestmark = [pytest.mark.integration, pytest.mark.x]\n", True),
        ("tests/test_a.py", "@pytest.mark.integration\nasync def test_x():\n    pass\n", True),
        ("tests/integration/test_a.py", "def test_x():\n    pass\n", True),
        ("tests/test_a.py", "TEXT = 'pytestmark = pytest.mark.integration'\n", False),
        ("tests/test_a.py", "pytestmark = pytest.mark.anyio\n", False),
        ("tests/authz/helper.py", "pytestmark = pytest.mark.integration\n", False),
    ],
)
def test_marker_scan_detects_integration_modules(path: str, source: str, expected: bool) -> None:
    assert is_integration_module(path, source) is expected


def test_registry_lists_every_new_integration_module_exactly_once() -> None:
    baseline = set(BASELINE.read_text().splitlines())
    current = integration_modules(BACKEND_ROOT)
    new_files = current - baseline
    assert len(COMPANY_ADMINISTRATION_MODULES) == len(set(COMPANY_ADMINISTRATION_MODULES))
    assert len(COMPANY_ADMINISTRATION_TARGETS) == len(set(COMPANY_ADMINISTRATION_TARGETS))
    missing = new_files - set(COMPANY_ADMINISTRATION_MODULES)
    orphan = set(COMPANY_ADMINISTRATION_MODULES) - new_files
    assert (sorted(missing), sorted(orphan)) == ([], [])
    assert baseline <= current


def test_runner_child_uses_only_the_fixed_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    dsns = build_lane_dsns(
        user="runner", password="dummy", port=49152, database="moldy_pg_lane_company"
    )
    environment = {
        "DATABASE_URL": dsns.async_url,
        "DATABASE_URL_SYNC": dsns.sync_url,
        "INTEGRATION_DATABASE_URL": dsns.integration_url,
    }
    commands: list[list[str]] = []

    def record(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        assert kwargs["env"] is environment
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("postgres_runner_runtime.run_command", record)
    assert run_test_child(environment, "company-administration") == (0, "")
    pytest_path = str(BACKEND_ROOT / ".venv/bin/pytest")
    assert commands == [company_administration_argv(pytest_path)]
    argv = commands[0]
    assert argv[:4] == [pytest_path, "-q", "-p", "tests.postgres_execution_plugin"]
    assert argv[-2:] == ["-m", "integration"]
    assert argv[4:-2] == list(COMPANY_ADMINISTRATION_TARGETS)


def test_shell_entrypoint_rejects_caller_supplied_paths(tmp_path: Path) -> None:
    script = REPO_ROOT / "scripts/run-isolated-postgres-tests.sh"
    manifest = str(tmp_path / "receipt.json")
    for argv in (
        ["company-administration", "tests/test_x.py", "--manifest", manifest],
        ["company-administration", "--manifest", manifest, "-k", "x"],
        ["company-admin", "--manifest", manifest],
    ):
        result = subprocess.run(  # noqa: S603 - fixed repository script
            ["/bin/bash", str(script), *argv], capture_output=True, text=True, check=False
        )
        assert result.returncode == 64, argv


def test_selection_gaps_reject_missing_and_foreign_nodes() -> None:
    selected = [f"{module}::test_case" for module in COMPANY_ADMINISTRATION_MODULES]
    selected += [f"{node}[param]" for node in COMPANY_ADMINISTRATION_LEGACY_NODES]
    assert company_selection_gaps(selected) == []
    assert company_selection_gaps(selected[1:]) == [f"missing:{COMPANY_ADMINISTRATION_MODULES[0]}"]
    foreign = "tests/integration/test_stream_resume.py::test_other"
    assert company_selection_gaps([*selected, foreign]) == [f"foreign:{foreign}"]


def test_fixed_argv_collects_every_target_without_skip_or_deselection() -> None:
    environment = {key: value for key, value in os.environ.items() if key not in _AMBIENT}
    environment["MOLDY_DISABLE_ENV_FILE"] = "true"
    argv = company_administration_argv(str(BACKEND_ROOT / ".venv/bin/pytest"))
    result = subprocess.run(  # noqa: S603 - fixed registry argv
        [*argv[:2], "--collect-only", "-p", "no:cacheprovider", *argv[2:]],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    nodes = [line for line in result.stdout.splitlines() if "::" in line]
    summary = result.stdout.strip().splitlines()[-1]
    assert company_selection_gaps(nodes) == []
    assert summary.startswith(f"{len(nodes)} tests collected"), summary
    assert "deselected" not in summary and "skipped" not in summary and "error" not in summary
    assert len(nodes) >= len(COMPANY_ADMINISTRATION_TARGETS)
