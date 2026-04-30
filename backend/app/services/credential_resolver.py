"""Single source of truth for "which credential should this model use" lookups.

The same tiered logic is needed by:
- ``POST /api/models/{id}/test``
- ``POST /api/health/check-now`` (model targets)
- ``ModelHealthPanel`` (frontend default selection — mirror in TS helper)
- ``agent_runtime.model_factory`` (when the agent's own
  ``llm_credential_id`` is unset)

Centralising the policy here ensures every new caller automatically respects
the user's "Add-model time" credential choice instead of falling back to env
or arbitrary first-match credentials.
"""

from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential import Credential
from app.models.model import Model


async def resolve_credential_for_model(
    db: AsyncSession,
    model: Model,
    explicit_credential_id: uuid.UUID | None,
    user_id: uuid.UUID,
) -> Credential | None:
    """Tiered credential lookup for a Model.

    1. ``explicit_credential_id`` — caller passed one (e.g. user picked a
       different credential in the UI for testing). Owner-checked.
    2. ``model.default_credential_id`` — captured at Add-model time.
       Owner-checked (NULL or wrong-owner falls through).
    3. ``None`` — caller decides whether to env-fallback or 4xx.

    Returns the Credential ORM row (caller decrypts) or None.
    """

    if explicit_credential_id is not None:
        cred = await credential_service.get_for_user(
            db, explicit_credential_id, user_id
        )
        if cred is not None:
            return cred
        # Explicit but invalid — surface to caller; do not silently fall
        # through to default. Mismatch likely means a stale UI state.
        return None

    if model.default_credential_id is not None:
        cred = await credential_service.get_for_user(
            db, model.default_credential_id, user_id
        )
        if cred is not None:
            return cred

    return None


def pick_default_for_provider(
    credentials: Iterable[Credential], provider: str
) -> Credential | None:
    """Frontend mirrors this in TS — server-side equivalent for tests.

    Used as a *display-time* fallback when no explicit/default credential
    exists. Picks the first credential whose ``definition_key`` matches the
    model's ``provider``; falls back to the first credential overall.
    """

    credentials = list(credentials)
    if not credentials:
        return None
    exact = next((c for c in credentials if c.definition_key == provider), None)
    return exact or credentials[0]


__all__ = ["resolve_credential_for_model", "pick_default_for_provider"]
