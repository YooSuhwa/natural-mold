"""OAuth2 helpers — token expiry detection and refresh delegation.

Concurrency note: this module is intentionally pure. The caller is responsible
for serializing concurrent refresh attempts on the same credential row, e.g.
by acquiring ``SELECT ... FOR UPDATE`` before invoking :func:`refresh_oauth_token`.
"""

from __future__ import annotations

import time
from typing import Any

from app.credentials.domain import CredentialDefinition


def _coerce_epoch(value: Any) -> float | None:
    """Best-effort coercion of an ``expires_at`` value to a unix timestamp."""

    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            from datetime import datetime

            try:
                return datetime.fromisoformat(value).timestamp()
            except ValueError:
                return None
    return None


def is_token_expired(
    credentials: dict[str, Any],
    expirable_field: str = "expires_at",
    skew_seconds: int = 60,
) -> bool:
    """Return True if the stored token is missing or about to expire.

    A missing or non-numeric ``expirable_field`` is treated as expired so the
    caller refreshes proactively on first use.
    """

    if not credentials.get("access_token"):
        return True

    epoch = _coerce_epoch(credentials.get(expirable_field))
    if epoch is None:
        return True
    return time.time() + skew_seconds >= epoch


async def refresh_oauth_token(
    definition: CredentialDefinition,
    credentials: dict[str, Any],
) -> dict[str, Any]:
    """Run the definition's ``pre_authentication`` hook and merge the result.

    Returns a new credential payload with refreshed token fields (typically
    ``access_token`` and ``expires_at``). The caller persists the result.

    Raises ``RuntimeError`` if the definition does not declare a hook.
    """

    if definition.pre_authentication is None:
        raise RuntimeError(
            f"credential definition '{definition.key}' has no pre_authentication hook"
        )

    patch = await definition.pre_authentication(dict(credentials))
    if not isinstance(patch, dict):
        raise RuntimeError(
            f"pre_authentication for '{definition.key}' must return a dict, got "
            f"{type(patch).__name__}"
        )

    refreshed = dict(credentials)
    refreshed.update(patch)
    return refreshed


__all__ = ["is_token_expired", "refresh_oauth_token"]
