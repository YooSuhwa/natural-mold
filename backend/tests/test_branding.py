"""Branding/license guard test — runs scripts/check_branding.py as a CI gate.

This test fails the test suite if forbidden identifiers, npm scopes, or asset
hashes are introduced. The single attribution file (NOTICES.md) and governance
documents are exempt by design — see scripts/check_branding.py for the policy.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_branding.py"


def test_branding_check_passes() -> None:
    """The branding guard must exit 0 against the current tree."""

    assert SCRIPT.exists(), f"branding script missing: {SCRIPT}"

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, (
        "branding check failed.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
