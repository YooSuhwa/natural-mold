"""Seed an OpenAI-compatible (LiteLLM) model for E2E (ADR-019 / ADR-013).

The conversational builder and the Assistant resolve their model from the
System LLM ``text_primary`` slot (``resolve_system_model``) and use the
*system* credential. But **user agents / subagents / chat** deliberately
refuse system credentials (``credential_resolution`` — operator-quota leak
guard) and require an *owner-registered* credential resolved by provider
match. So to exercise every LLM-decision flow end-to-end we provision both.

When ``E2E_LLM_BASE_URL`` / ``E2E_LLM_API_KEY`` / ``E2E_LLM_MODEL`` are all set
(idempotently, dev-only):

  1. an ``is_system`` ``openai_compatible`` credential   -> Builder / Assistant,
  2. a user-owned ``openai_compatible`` credential for the seeded E2E user
     -> user-agent / subagent / chat (provider-matched, tier-3),
  3. a ``Model`` row (default credential = the user one, so the E2E user's
     agents resolve cleanly without a per-agent binding),
  4. ``system_llm_settings`` ``text_primary`` + ``text_fallback`` -> the model.

Secrets stay in env (never committed). Skipped in production / when unset.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import assert_never

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext, resolve_org_context
from app.authz.platform_scope import PlatformScope
from app.authz.scoping import private_scoped
from app.config import settings
from app.credentials import service as credential_service
from app.credentials import tenant_encryption
from app.credentials.creation import CredentialCreation
from app.dependencies import CurrentUser
from app.models.credential import Credential
from app.models.model import Model
from app.models.system_llm_setting import SystemLlmSetting
from app.models.user import User
from app.seed.e2e_scope import E2E_ORG_ID

logger = logging.getLogger(__name__)

E2E_LLM_DEFINITION_KEY = "openai_compatible"
E2E_LLM_SYSTEM_CREDENTIAL_NAME = "[e2e] LiteLLM"
E2E_LLM_USER_CREDENTIAL_NAME = "[e2e] LiteLLM (user)"
_E2E_LLM_ROLES = ("text_primary", "text_fallback")


async def _upsert_credential(
    db: AsyncSession,
    creation: CredentialCreation,
    context: OrgContext | PlatformScope,
) -> Credential:
    """Find-by-name, refreshing the encrypted payload so changed env values
    take effect on re-seed; create it if absent."""
    query = select(Credential).where(Credential.name == creation.name)
    match context:
        case OrgContext():
            query = private_scoped(query, Credential, context)
        case PlatformScope():
            query = query.where(
                Credential.is_system.is_(True),
                Credential.org_id.is_(None),
                Credential.tenant_id.is_(None),
            )
        case unreachable:
            assert_never(unreachable)
    cred = (await db.execute(query.limit(1))).scalar_one_or_none()
    data = dict(creation.data)
    if cred is None:
        cred = await credential_service.create(
            db,
            creation,
            context,
        )
        logger.info("seed_e2e_llm: created credential %s", creation.name)
    else:
        await tenant_encryption.seal(cred, data, db=db)
        await db.flush()
        logger.info("seed_e2e_llm: refreshed credential %s", creation.name)
    return cred


async def seed_e2e_llm(db: AsyncSession) -> Model | None:
    if settings.app_env == "production":
        logger.warning("skip seed_e2e_llm: production environment")
        return None

    base_url = (settings.e2e_llm_base_url or "").strip()
    api_key = (settings.e2e_llm_api_key or "").strip()
    model_name = (settings.e2e_llm_model or "").strip()
    if not (base_url and api_key and model_name):
        return None

    # 1) system credential -> Builder / Assistant (system models).
    system_cred = await _upsert_credential(
        db,
        CredentialCreation(
            definition_key=E2E_LLM_DEFINITION_KEY,
            name=E2E_LLM_SYSTEM_CREDENTIAL_NAME,
            data={"base_url": base_url, "api_key": api_key},
            source="seed",
        ),
        PlatformScope(),
    )

    # 2) user-owned credential for the seeded E2E user -> user agents / chat.
    e2e_user = (
        await db.execute(select(User).where(User.email == settings.e2e_user_email).limit(1))
    ).scalar_one_or_none()
    user_cred: Credential | None = None
    if e2e_user is not None:
        actor = CurrentUser(
            id=e2e_user.id,
            email=e2e_user.email,
            name=e2e_user.name,
            is_super_user=e2e_user.is_super_user,
        )
        context = await resolve_org_context(db, actor, cookie=str(E2E_ORG_ID))
        user_cred = await _upsert_credential(
            db,
            CredentialCreation(
                definition_key=E2E_LLM_DEFINITION_KEY,
                name=E2E_LLM_USER_CREDENTIAL_NAME,
                data={"base_url": base_url, "api_key": api_key},
                source="seed",
            ),
            context,
        )

    # 3) Model row. Default credential = the user one when available so the E2E
    # user's agents resolve via tier-2 without a per-agent binding.
    default_credential_id = user_cred.id if user_cred is not None else system_cred.id
    model = (
        await db.execute(
            select(Model)
            .where(Model.provider == E2E_LLM_DEFINITION_KEY)
            .where(Model.model_name == model_name)
            .limit(1)
        )
    ).scalar_one_or_none()
    if model is None:
        model = Model(
            provider=E2E_LLM_DEFINITION_KEY,
            model_name=model_name,
            display_name=f"E2E LiteLLM ({model_name})",
            base_url=base_url,
            default_credential_id=default_credential_id,
            is_default=False,
            is_visible=True,
            cost_per_input_token=Decimal("0"),
            cost_per_output_token=Decimal("0"),
            supports_function_calling=True,
            input_modalities=["text"],
            output_modalities=["text"],
            source="seed",
        )
        db.add(model)
        await db.flush()
        logger.info("seed_e2e_llm: created model %s", model_name)
    else:
        model.base_url = base_url
        model.default_credential_id = default_credential_id
        model.is_visible = True
        await db.flush()

    # 4) System LLM text_primary + text_fallback -> system credential + model.
    for role in _E2E_LLM_ROLES:
        setting = (
            await db.execute(select(SystemLlmSetting).where(SystemLlmSetting.role == role).limit(1))
        ).scalar_one_or_none()
        if setting is None:
            db.add(SystemLlmSetting(role=role, credential_id=system_cred.id, model_name=model_name))
        else:
            setting.credential_id = system_cred.id
            setting.model_name = model_name
    await db.flush()
    logger.info("seed_e2e_llm: text_primary/text_fallback -> %s (%s)", model_name, base_url)
    return model


__all__ = [
    "E2E_LLM_SYSTEM_CREDENTIAL_NAME",
    "E2E_LLM_USER_CREDENTIAL_NAME",
    "seed_e2e_llm",
]
