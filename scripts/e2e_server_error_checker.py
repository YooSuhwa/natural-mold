"""Detect database lifecycle failures that background tasks can swallow."""

from __future__ import annotations

from enum import StrEnum


class DatabaseLifecycleError(StrEnum):
    DEADLOCK = "database_deadlock"
    FOREIGN_KEY = "database_foreign_key_violation"


def database_lifecycle_errors(stderr: str) -> frozenset[DatabaseLifecycleError]:
    markers = {
        DatabaseLifecycleError.DEADLOCK: (
            "DeadlockDetectedError",
            "psycopg.errors.DeadlockDetected",
        ),
        DatabaseLifecycleError.FOREIGN_KEY: (
            "ForeignKeyViolationError",
            "psycopg.errors.ForeignKeyViolation",
        ),
    }
    return frozenset(
        code for code, names in markers.items() if any(name in stderr for name in names)
    )
