"""Regression guard for the low-noise ruff rule batch — lint plan item E.

CI-gate rule (CLAUDE.md): when a lint gate gains exemptions, commit
negative regression tests proving that blocked cases are still blocked.
Item E enables ``DTZ, C4, SLF, RET, PTH, PT, N`` with these exemptions in
``pyproject.toml``:

* global ignore ``N818`` (codebase-wide domain-style exception names),
* ``app/**`` ignores ``PT`` (pytest-style rules false-positive on FastAPI
  endpoints named ``test_*``),
* ``tests/*`` ignores ``SLF001, DTZ, PT017, PT018, N801, N815`` (test
  idioms / wire-contract mocks).

These tests pin that each exemption is rule-scoped, not a blanket
disable: the batch actually turns the gate red where it should, and the
exempted paths still fail on sibling rules from the same batch.

``--stdin-filename`` makes per-file-ignores resolve as if the probe
lived at the given path, so no violation fixture needs to exist on disk
(which the gate itself would otherwise scan and flag).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
# Prefer the project venv's ruff (CI installs it via `uv sync --all-extras`)
# so the test measures the same pinned version the CI gate runs; fall back
# to PATH for local venvs synced without the dev extra.
_VENV_RUFF = Path(sys.executable).parent / "ruff"
RUFF = str(_VENV_RUFF) if _VENV_RUFF.exists() else shutil.which("ruff")

pytestmark = pytest.mark.skipif(
    RUFF is None, reason="ruff binary not on PATH (install the dev extra)"
)

# Probes — each triggers exactly the rule named in the comment.
DATE_TODAY = "import datetime\n\nWINDOW = datetime.date.today()\n"  # DTZ011
PRIVATE_ACCESS = "def f(obj):\n    return obj._secret\n"  # SLF001
NEEDLESS_ASSIGN = "def f():\n    x = 1\n    return x\n"  # RET504
OS_PATH_EXISTS = "import os\n\nFOUND = os.path.exists('/tmp/x')\n"  # PTH110
# PT028 (endpoint named test_* with default args) — app/ false-positive shape.
ENDPOINT_TEST_NAME = "def test_connection(db=None):\n    return db\n"
# PT018 (composite assertion) — exempted in tests/ (S101 assert also exempt there).
COMPOSITE_ASSERT = "def test_x():\n    a = 1\n    assert a and a > 0\n"
# N801 (non-CapWords class) — exempted in tests/ for scenario class names.
SCENARIO_CLASS = "class TestScenario_1_Probe:\n    pass\n"


def _ruff_check(stdin_filename: str, code: str) -> subprocess.CompletedProcess[str]:
    assert RUFF is not None
    return subprocess.run(
        [RUFF, "check", "--no-cache", "--stdin-filename", stdin_filename, "-"],
        input=code,
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize(
    ("code", "probe", "expected_rule"),
    [
        (DATE_TODAY, "date.today", "DTZ011"),
        (PRIVATE_ACCESS, "private access", "SLF001"),
        (NEEDLESS_ASSIGN, "needless assign", "RET504"),
        (OS_PATH_EXISTS, "os.path", "PTH110"),
    ],
)
def test_batch_violation_in_app_turns_gate_red(code: str, probe: str, expected_rule: str) -> None:
    result = _ruff_check("app/_lint_probe.py", code)
    assert result.returncode != 0, f"{probe}: {result.stdout}"
    assert expected_rule in result.stdout, f"{probe}: {result.stdout}"


def test_app_pt_exemption_silences_endpoint_false_positive() -> None:
    result = _ruff_check("app/routers/_lint_probe.py", ENDPOINT_TEST_NAME)
    assert "PT028" not in result.stdout, result.stdout


def test_app_pt_exemption_is_not_a_blanket_disable() -> None:
    # The same app/ path must still fail sibling rules from the batch.
    result = _ruff_check("app/routers/_lint_probe.py", NEEDLESS_ASSIGN)
    assert result.returncode != 0, result.stdout
    assert "RET504" in result.stdout


@pytest.mark.parametrize(
    ("code", "exempted_rule"),
    [
        (DATE_TODAY, "DTZ011"),
        (PRIVATE_ACCESS, "SLF001"),
        (COMPOSITE_ASSERT, "PT018"),
        (SCENARIO_CLASS, "N801"),
    ],
)
def test_tests_exemptions_are_rule_scoped(code: str, exempted_rule: str) -> None:
    result = _ruff_check("tests/_lint_probe.py", code)
    assert exempted_rule not in result.stdout, result.stdout


def test_tests_exemption_is_not_a_blanket_disable() -> None:
    # tests/ still fails on batch rules outside its exemption list.
    for code, rule in ((NEEDLESS_ASSIGN, "RET504"), (OS_PATH_EXISTS, "PTH110")):
        result = _ruff_check("tests/_lint_probe.py", code)
        assert result.returncode != 0, f"{rule}: {result.stdout}"
        assert rule in result.stdout, f"{rule}: {result.stdout}"
