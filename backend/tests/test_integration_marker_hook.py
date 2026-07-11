"""Regression guard for the auto-applied ``integration`` marker (plan item G).

Two failure modes this pins (both are silent — pytest exits 0 on full
deselection, so CI would stay green while running nothing):

* the ``tests/integration/conftest.py`` hook stops applying the marker →
  timing-sensitive tests silently rejoin the xdist parallel suite (the
  PR #280/#282 flake class comes back), and
* the serial CI step's ``-m integration`` selects zero tests → the whole
  integration suite silently stops running.

Runs pytest in collect-only mode as a subprocess so the assertion sees the
same config (addopts, conftest chain) as the real CI steps.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _collect_only(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _collected_count(stdout: str) -> int:
    return sum(1 for line in stdout.splitlines() if "::" in line and not line.startswith("="))


def test_every_integration_test_gets_the_marker_auto_applied() -> None:
    # `-m integration` keeps only marked tests; the hook must cover the whole
    # directory, so selecting by marker and selecting by path must agree.
    marked = _collect_only("tests/integration", "-m", "integration")
    unfiltered = _collect_only("tests/integration", "-m", "integration or not integration")
    assert marked.returncode == 0, marked.stdout + marked.stderr
    n_marked = _collected_count(marked.stdout)
    n_all = _collected_count(unfiltered.stdout)
    assert n_marked == n_all, (
        f"{n_all - n_marked} test(s) in tests/integration/ escaped the auto marker "
        "— the conftest hook regressed"
    )
    # More than the single hand-marked m9 test → the hook (not pytestmark) did it.
    assert n_marked > 1, marked.stdout


def test_serial_selection_is_not_a_silent_false_green() -> None:
    # Without `-m integration` the addopts filter deselects everything and
    # pytest still exits 0 — this is exactly what the CI serial step must NOT
    # rely on. Pin the deselection so a future addopts change that silently
    # re-enables parallel collection of these tests gets noticed.
    plain = _collect_only("tests/integration")
    # Full deselection: collect-only exits 5 (no tests collected) while a
    # real run exits 0 — which is exactly why the false green is silent.
    assert plain.returncode in (0, 5), plain.stdout + plain.stderr
    assert _collected_count(plain.stdout) == 0, (
        "default addopts should deselect tests/integration entirely; "
        "if this fails, revisit the CI serial step and xdist split"
    )
