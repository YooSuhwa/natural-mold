"""Legacy invariants stub — preserved for frozen alembic history compatibility.

The original implementation enforced preflight checks on legacy
`tools.credential_id` / `tools.auth_config` / `agent_tools.config` rows before
m12 dropped those columns. After M5 of the greenfield rewrite, those tables
were fully replaced (m18) and the original logic was removed.

This stub stays so that the historical migration script
`alembic/versions/m12_drop_legacy_columns.py` can still `import
app.services.legacy_invariants` without crashing during a fresh
`alembic upgrade head`. It returns no checks, so the m12 preflight passes
trivially — which is correct, because greenfield databases have no legacy
rows by construction.

Do not extend this module. New invariants belong in dedicated services.
"""

from __future__ import annotations

from collections.abc import Callable

LegacyCheck = tuple[str, str]


def collect_legacy_checks(
    dialect_name: str,  # noqa: ARG001 — kept for signature compatibility
    column_exists: Callable[[str, str], bool],  # noqa: ARG001
) -> list[LegacyCheck]:
    """Return an empty list of preflight checks.

    The historical m12 migration calls this to assert no stale legacy rows
    exist before dropping columns. Post-M5 there is nothing to enforce — m18
    drops and recreates the affected tables wholesale.
    """

    return []
