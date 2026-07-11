"""Auto-apply the ``integration`` marker to every test in this directory.

Root cause of the PR #280/#282 CI flakes (lint plan item G): timing-sensitive
tests lived in ``tests/integration/`` without the marker, so they ran inside
the xdist parallel suite and starved on 2-core runners. The marker used to be
opt-in per file (only ``test_m9_pg_roundtrip`` carried it) — this hook makes
directory placement authoritative so a forgotten marker can't happen again.

Interaction contract (see ``pyproject.toml`` ``addopts = "-m 'not
integration'"``): the default run now skips this whole directory, so the
serial runner MUST select it explicitly::

    uv run pytest -q tests/integration -m integration

(the trailing ``-m`` overrides addopts. Plain ``pytest tests/integration``
deselects everything and exits 5 — loud in a dir-scoped CI step; the silent
failure mode is a full-suite ``pytest tests/`` run, where passing sibling
tests mask the deselection with exit 0. Guarded by
``tests/test_integration_marker_hook.py``.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

_INTEGRATION_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.is_relative_to(_INTEGRATION_DIR):
            item.add_marker(pytest.mark.integration)
