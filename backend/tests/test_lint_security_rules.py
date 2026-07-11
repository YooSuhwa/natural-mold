"""Regression guard for the ruff bandit (``S``) gate — lint plan item C.

CI-gate rule (CLAUDE.md): when a lint gate gains exemptions, commit
negative regression tests proving that blocked cases are still blocked.
The exemptions here are the ``tests/*`` / ``alembic/versions/*``
per-file-ignores in ``pyproject.toml``. These tests pin that:

* an ``S`` violation under ``app/`` turns the gate red (the rule is
  actually selected, not report-only),
* the ``tests/*`` exemption is rule-scoped (``S101`` allowed) and not a
  blanket disable (``S110`` still fires there).

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

# S110 probe — try/except/pass is not exempted anywhere.
TRY_EXCEPT_PASS = "try:\n    pass\nexcept Exception:\n    pass\n"


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


def test_s_violation_in_app_turns_gate_red() -> None:
    result = _ruff_check("app/_lint_probe.py", TRY_EXCEPT_PASS)
    assert result.returncode != 0, result.stdout
    assert "S110" in result.stdout


def test_tests_exemption_allows_pytest_assert() -> None:
    result = _ruff_check("tests/_lint_probe.py", "assert 1 == 1\n")
    assert result.returncode == 0, result.stdout


def test_tests_exemption_is_not_a_blanket_disable() -> None:
    result = _ruff_check("tests/_lint_probe.py", TRY_EXCEPT_PASS)
    assert result.returncode != 0, result.stdout
    assert "S110" in result.stdout


def test_blanket_noqa_is_rejected_everywhere() -> None:
    # PGH004 (plan item F): a bare `# noqa` without codes must fail — even in
    # tests/, which only exempts specific S rules.
    for path in ("app/_lint_probe.py", "tests/_lint_probe.py"):
        result = _ruff_check(path, "import os  # noqa\n")
        assert result.returncode != 0, f"{path}: {result.stdout}"
        assert "PGH004" in result.stdout, f"{path}: {result.stdout}"
