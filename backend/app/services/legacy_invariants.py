"""M6 legacy-row invariants shared between alembic m12 and app startup guard.

m12 migration과 app startup guard는 동일한 legacy auth dirty-row 집합을
검증한다. 한 곳에서 정의해야 SQL/label drift 없이 양쪽 invariant가 정합.

Callers:
  - `alembic/versions/m12_drop_legacy_columns.py::_assert_no_stale_legacy_rows`
    (sync `bind.execute`)
  - `app/main.py::lifespan` M6 deploy-order guard (async `db.execute`)
"""

from __future__ import annotations

from collections.abc import Callable


def _non_empty_json(column: str, dialect: str) -> str:
    if dialect == "postgresql":
        return f"{column} IS NOT NULL AND {column}::text != '{{}}'::text"
    return f"{column} IS NOT NULL AND {column} != '{{}}'"


def collect_legacy_checks(
    dialect: str,
    column_exists: Callable[[str, str], bool],
) -> list[tuple[str, str]]:
    """Return `[(label, sql)]` pairs whose COUNT must be 0 before M6 runtime.

    `column_exists(table, column) -> bool` is caller-provided so the same
    helper works for sync (alembic Inspector) and async (information_schema)
    checks.
    """
    checks: list[tuple[str, str]] = []

    if column_exists("agent_tools", "config"):
        checks.append(
            (
                "agent_tools rows with non-empty legacy config override",
                f"SELECT COUNT(*) FROM agent_tools WHERE {_non_empty_json('config', dialect)}",
            )
        )

    if column_exists("tools", "credential_id"):
        checks.append(
            (
                "CUSTOM tools with legacy credential_id but no connection_id",
                "SELECT COUNT(*) FROM tools "
                "WHERE type = 'custom' "
                "AND credential_id IS NOT NULL AND connection_id IS NULL",
            )
        )
        checks.append(
            (
                "CUSTOM tools with bridge override "
                "(tool.credential_id != connection.credential_id)",
                "SELECT COUNT(*) FROM tools t "
                "JOIN connections c ON t.connection_id = c.id "
                "WHERE t.type = 'custom' "
                "AND t.credential_id IS NOT NULL "
                "AND c.credential_id IS NOT NULL "
                "AND t.credential_id != c.credential_id",
            )
        )

    if column_exists("tools", "auth_config"):
        checks.append(
            (
                "tools with non-empty legacy auth_config",
                f"SELECT COUNT(*) FROM tools WHERE {_non_empty_json('auth_config', dialect)}",
            )
        )

    checks.append(
        (
            "PREBUILT tools with NULL provider_name",
            "SELECT COUNT(*) FROM tools "
            "WHERE type = 'prebuilt' AND provider_name IS NULL",
        )
    )
    checks.append(
        (
            "CUSTOM tools with no connection_id (dead after M6)",
            "SELECT COUNT(*) FROM tools "
            "WHERE type = 'custom' AND connection_id IS NULL",
        )
    )

    return checks
