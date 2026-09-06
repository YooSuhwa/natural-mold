"""Auto-apply the ``integration`` marker to every test in this directory.

Root cause of the PR #280/#282 CI flakes (lint plan item G): timing-sensitive
tests lived in ``tests/integration/`` without the marker, so they ran inside
the xdist parallel suite and starved on 2-core runners. The marker used to be
opt-in per file (only ``test_m9_pg_roundtrip`` carried it) — this hook makes
directory placement authoritative so a forgotten marker can't happen again.

Interaction contract (see ``pyproject.toml`` ``addopts = "-m 'not
integration'"``): the default run now skips this whole directory, so the
canonical disposable PostgreSQL runner MUST select integration tests explicitly::

    manifest=".omo/evidence/project-restart-consolidated-roadmap/local-postgres-$(date +%s).json"
    bash scripts/run-isolated-postgres-tests.sh all --manifest "$manifest"
    (cd backend && uv run python ../scripts/check-isolation-cleanup.py "../$manifest")

The runner invokes ``pytest tests -m integration`` so every marked integration
node is included, including marked tests outside this directory. The trailing
``-m`` overrides addopts. Plain ``pytest tests/integration`` deselects everything
and exits 5 — loud in a dir-scoped debug run; the silent failure mode is a
full-suite ``pytest tests/`` run, where passing sibling tests mask the
deselection with exit 0. Guarded by ``tests/test_integration_marker_hook.py``.

For focused debugging, ``uv run pytest -q tests/integration -m integration`` is
still valid from ``backend/``, but it is not the canonical lifecycle gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_INTEGRATION_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.path.is_relative_to(_INTEGRATION_DIR):
            item.add_marker(pytest.mark.integration)
