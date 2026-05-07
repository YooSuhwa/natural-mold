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

    INFO 레벨 trace 를 한 줄씩 emit — 어느 단계에서 키가 결정/실패했는지
    backend stdout 로 진단 가능 (silent fail 회귀 방지).
    """

    cred = getattr(agent, "llm_credential", None)
    if cred is not None:
        key = await _decrypt_api_key(cred)
        if key is not None:
            logger.info("agent %s: api_key from agent.llm_credential", agent.id)
            return key
        logger.warning(
            "agent %s: llm_credential decrypt returned None, trying model.default",
            agent.id,
        )

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
                logger.info(
                    "agent %s: api_key from model.default_credential (id=%s)",
                    agent.id,
                    fallback_cred.id,
                )
                return key
            logger.warning(
                "agent %s: model.default_credential decrypt returned None",
                agent.id,
            )
        else:
            logger.warning(
                "agent %s: model.default_credential_id=%s not owned by user %s",
                agent.id,
                model.default_credential_id,
                agent.user_id,
            )

    logger.warning(
        "agent %s: no LLM credential resolved — falling back to env (api_key=None)",
        agent.id,
    )
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
