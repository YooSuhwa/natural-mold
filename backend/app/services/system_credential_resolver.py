"""Single source of truth for "operator-managed API key" lookups.

Used by every system-billed flow:
- Fix Agent (``app.agent_runtime.assistant.assistant_agent``)
- Image generation (``app.services.image_service``,
  ``app.agent_runtime.builder_v3.image_gen``)
- Future builder / bootstrap flows

Tiered policy (mirror of the user-side
:func:`app.services.credential_resolver.resolve_credential_for_model`):

  1. ENV (``PROVIDER_API_KEY_MAP``) — bootstrap convenience so a clean
     install with only ``.env`` keeps working.
  2. ``Credential`` row with ``is_system=True`` matching the provider —
     operator manages keys via ``/settings/system-credentials``.
  3. ``None`` — caller surfaces the resulting LLM error.

User credentials are intentionally NOT consulted. System functions bill
the operator, not whichever user happens to be logged in.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.model_factory import PROVIDER_API_KEY_MAP
from app.credentials import service as credential_service

logger = logging.getLogger(__name__)


async def resolve_system_api_key(
    db: AsyncSession, provider: str
) -> str | None:
    """ENV → ``is_system=True`` Credential lookup → ``None``."""

    env_key = PROVIDER_API_KEY_MAP.get(provider)
    if env_key:
        return env_key

    cred = await credential_service.find_system_by_definition(db, provider)
    if cred is None:
        return None
    try:
        payload = await credential_service.decrypt_with_external(
            cred.data_encrypted
        )
    except Exception:  # noqa: BLE001
        logger.exception("System credential %s decryption failed", cred.id)
        return None
    api_key = payload.get("api_key") or payload.get("token")
    return str(api_key) if api_key else None


__all__ = ["resolve_system_api_key"]
