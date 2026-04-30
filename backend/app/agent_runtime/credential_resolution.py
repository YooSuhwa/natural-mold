"""Agent → LLM API key resolution.

Single source of truth for "which API key should this agent's runtime use".
Mirrors :func:`app.services.credential_resolver.resolve_credential_for_model`
but operates on an :class:`Agent` (with eager-loaded ``llm_credential`` and
``model``) and returns the *decrypted* api_key string ready to drop into
``ChatOpenAI``/``ChatAnthropic`` etc.

Tiered policy:
  1. ``agent.llm_credential`` (the agent's explicit binding).
  2. ``agent.model.default_credential_id`` (captured at Add-model time —
     respects user intent without forcing every agent to re-bind).
  3. ``None`` — caller falls back to env-var (Builder/Assistant sub-agents
     and bootstrap flows).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.agent import Agent
from app.models.credential import Credential

logger = logging.getLogger(__name__)


async def resolve_llm_api_key_for_agent(
    db: AsyncSession,
    agent: Agent,
) -> str | None:
    """Decrypt the best available credential for ``agent`` and return its key.

    Returns ``None`` when no usable credential exists; the model factory will
    fall through to env-var fallback.
    """

    cred = getattr(agent, "llm_credential", None)
    if cred is not None:
        key = await _decrypt_api_key(cred)
        if key is not None:
            return key

    model = getattr(agent, "model", None)
    if model is not None and model.default_credential_id is not None:
        # Same-user check is implicit: the model belongs to the agent's user
        # and the FK was set by that user — but we still verify ownership
        # before decrypting in case of legacy data.
        fallback_cred = await credential_service.get_for_user(
            db, model.default_credential_id, agent.user_id
        )
        if fallback_cred is not None:
            key = await _decrypt_api_key(fallback_cred)
            if key is not None:
                return key

    return None


async def _decrypt_api_key(cred: Credential) -> str | None:
    """Decrypt and pluck out the conventional ``api_key`` / ``token`` field."""

    try:
        payload = await credential_service.decrypt_with_external(cred.data_encrypted)
    except Exception:  # noqa: BLE001 — surface as missing key
        logger.exception("LLM credential %s decryption failed", cred.id)
        return None
    api_key = payload.get("api_key") or payload.get("token")
    return str(api_key) if api_key else None


__all__ = ["resolve_llm_api_key_for_agent"]
