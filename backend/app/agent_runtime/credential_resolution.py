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
  3. Provider-matched user-owned credential.

Missing user credentials raise ``LLMCredentialRequiredError``. Builder,
Assistant, and other service flows use ``system_credential_resolver`` instead.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.identity import AgentRunIdentity
from app.config import settings
from app.credentials import service as credential_service
from app.credentials.service import PROVIDER_TO_DEFINITION_KEY
from app.exceptions import AppError
from app.models.agent import Agent
from app.models.credential import Credential

logger = logging.getLogger(__name__)


class LLMCredentialRequiredError(AppError):
    """Raised when an agent chat reaches LLM call with no user-owned key.

    User-facing agent chats — for *every* user, including super_users —
    must run on a credential the agent's owner personally registered.
    System credentials are reserved for operator-managed service flows
    (Fix Agent / builder / image generation) routed through a separate
    resolver. Returning ``None`` here would silently fall back to the
    operator's key for super_users too, billing them personally. See
    ADR-016 §4.2.
    """

    def __init__(self) -> None:
        super().__init__(
            code="llm_credential_required",
            message=(
                "본인의 LLM API 키가 등록되어 있지 않습니다. "
                "/credentials 페이지에서 키를 등록한 뒤 다시 시도해주세요."
            ),
            status=422,
        )


async def resolve_llm_api_key_for_agent(
    db: AsyncSession,
    agent: Agent,
    *,
    identity: AgentRunIdentity | None = None,
) -> str | None:
    """Decrypt the best available credential for ``agent`` and return its key.

    Raises ``LLMCredentialRequiredError`` when no usable user-owned credential
    exists.

    INFO 레벨 trace 를 한 줄씩 emit — 어느 단계에서 키가 결정/실패했는지
    backend stdout 로 진단 가능 (silent fail 회귀 방지).
    """

    subject_user_id = identity.credential_subject_user_id if identity is not None else agent.user_id
    model = getattr(agent, "model", None)
    if _allows_keyless_dev_model(model):
        logger.info(
            "agent %s: keyless dev model provider=%s",
            agent.id,
            model.provider,
        )
        return None

    cred = getattr(agent, "llm_credential", None)
    if cred is not None:
        if _credential_owned_by_subject(cred, subject_user_id):
            key = await _decrypt_api_key(cred)
            if key is not None:
                logger.info(
                    "agent %s: api_key from agent.llm_credential for subject %s",
                    agent.id,
                    subject_user_id,
                )
                return key
        else:
            logger.warning(
                "agent %s: llm_credential_id=%s not owned by credential subject %s",
                agent.id,
                cred.id,
                subject_user_id,
            )
            cred = None
    if cred is not None:
        logger.warning(
            "agent %s: llm_credential decrypt returned None, trying model.default",
            agent.id,
        )

    if model is not None and model.default_credential_id is not None:
        fallback_cred = await credential_service.get_for_user(
            db, model.default_credential_id, subject_user_id
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
                subject_user_id,
            )

    # 3rd tier — auto-match user-owned credential by provider. The two
    # explicit binding points above (agent.llm_credential, model.default)
    # remain authoritative; this only kicks in when both are NULL and the
    # user has *exactly one obvious choice*: a credential whose
    # ``definition_key`` matches the model's ``provider``. Avoids forcing
    # users to manually re-bind after registering a key. If the user owns
    # multiple credentials for the same provider, picks the most recent so
    # post-rotation flows pick up the new key automatically.
    if model is not None and model.provider in PROVIDER_TO_DEFINITION_KEY:
        definition_key = PROVIDER_TO_DEFINITION_KEY[model.provider]
        matches = (
            await db.execute(
                select(Credential)
                .where(
                    Credential.user_id == subject_user_id,
                    Credential.is_system.is_(False),
                    Credential.definition_key == definition_key,
                    Credential.status == "active",
                )
                .order_by(Credential.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if matches is not None:
            key = await _decrypt_api_key(matches)
            if key is not None:
                logger.info(
                    "agent %s: api_key from provider-matched user credential"
                    " (id=%s, definition=%s)",
                    agent.id,
                    matches.id,
                    definition_key,
                )
                return key

    # No user-owned credential found. The model factory's env fallback would
    # otherwise serve up the operator's system credential (ADR-013 sync),
    # leaking operator quota to whoever is chatting — *including* super_users,
    # who'd pay the operator's bill for their own personal chats. System
    # credentials are reserved for service flows (builder/assistant/image
    # gen) routed through ``system_credential_resolver``. Agent chat always
    # demands an owner-registered key; raise a clear 422 with guidance.
    logger.info(
        "agent %s: no user-owned LLM credential for owner %s — raising",
        agent.id,
        subject_user_id,
    )
    raise LLMCredentialRequiredError()


def _allows_keyless_dev_model(model: object | None) -> bool:
    return (
        model is not None
        and getattr(model, "provider", None) == "e2e_scripted"
        and settings.e2e_scripted_model_enabled
        and settings.app_env.lower() != "production"
    )


def _credential_owned_by_subject(cred: Credential, subject_user_id: uuid.UUID) -> bool:
    return (
        cred.user_id == subject_user_id
        and bool(getattr(cred, "is_system", False)) is False
        and getattr(cred, "status", "active") == "active"
    )


async def _decrypt_api_key(cred: Credential) -> str | None:
    """Decrypt and pluck out the conventional ``api_key`` / ``token`` field."""

    try:
        payload = await credential_service.decrypt_with_external(cred.data_encrypted)
    except Exception:  # noqa: BLE001 — surface as missing key
        logger.exception("LLM credential %s decryption failed", cred.id)
        return None
    api_key = payload.get("api_key") or payload.get("token")
    return str(api_key) if api_key else None


__all__ = ["resolve_llm_api_key_for_agent", "LLMCredentialRequiredError"]
