"""Regression guard for the auto-applied ``integration`` marker (plan item G).

Failure modes this pins:

* the ``tests/integration/conftest.py`` hook stops applying the marker →
  timing-sensitive tests silently rejoin the xdist parallel suite (the
  PR #280/#282 flake class comes back — no red, just flakes), and
* the deselection contract drifts: a dir-scoped plain run must deselect
  everything and exit 5 (NO_TESTS_COLLECTED — loud in CI), while the truly
  silent variant is a full-suite ``pytest tests/`` run where passing sibling
  tests mask the deselection with exit 0. The CI serial step therefore
  selects ``-m integration`` explicitly.

Runs pytest in collect-only mode as a subprocess so the assertion sees the
same config (addopts, conftest chain) as the real CI steps.

Cost note: the three subprocesses add ~11s to the suite. That is pytest
bootstrap overhead (~3.4s each), not conftest imports — measured that
``--confcutdir=tests/integration`` does not reduce total time (collection
itself is <0.1s) — so there is no cheap optimization; the fidelity of
running the real config is worth the cost.
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


def test_plain_serial_invocation_collects_nothing_and_is_loud() -> None:
    # Without `-m integration` the addopts filter deselects the whole
    # directory and pytest exits 5 (NO_TESTS_COLLECTED) — a dir-scoped CI
    # step would fail loudly, which is why the serial step carries the
    # explicit `-m`. Pin both halves of the contract: zero collection AND
    # the non-zero exit (measured without a pipeline — `cmd | tail` eats
    # the exit code).
    plain = _collect_only("tests/integration")
    assert plain.returncode == 5, plain.stdout + plain.stderr
    assert _collected_count(plain.stdout) == 0, (
        "default addopts should deselect tests/integration entirely; "
        "if this fails, revisit the CI serial step and xdist split"
    )
