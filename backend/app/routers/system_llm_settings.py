"""System LLM settings router (ADR-019) — operator-only role→model selection.

Every endpoint is guarded by ``require_super_user``. The screen lets an operator
pick, per role (text_primary / text_fallback / image), a system LLM credential
and a model discovered from it (via the existing
``POST /api/credentials/{id}/discover-models``). Credential CRUD stays on the
existing System Credentials screen (ADR-019 §결정4).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.dependencies import (
    CurrentUser,
    get_db,
    require_super_user,
    verify_csrf,
)
from app.models.system_llm_setting import SYSTEM_LLM_ROLES, SystemLlmSetting
from app.schemas.system_llm_setting import (
    SystemLlmSettingOut,
    SystemLlmSettingUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system-llm-settings", tags=["system-llm-settings"])

# Credential definition_keys that are valid LLM providers for a system slot.
# (ADR-019 — openai / anthropic / openrouter / litellm-style openai_compatible)
_LLM_DEFINITION_KEYS = frozenset(
    {"openai", "anthropic", "openrouter", "openai_compatible"}
)

# Unified message for any invalid credential selection. Distinguishing
# "missing" from "wrong type" only in the server log avoids leaking which
# system credentials exist (enumeration oracle, security.md).
_INVALID_CREDENTIAL_DETAIL = (
    "credential_id must reference an existing system LLM credential"
)


async def _build_out(
    db: AsyncSession, setting: SystemLlmSetting
) -> SystemLlmSettingOut:
    """Materialize a settings row into the API shape.

    ``provider`` comes from ``credential.definition_key`` (no decrypt).
    ``base_url`` requires a decrypt; at most one per configured role (3 roles
    total) so there is no N+1 concern. Decryption failures degrade to NULL
    rather than failing the whole list.
    """

    credential_name: str | None = None
    provider: str | None = None
    base_url: str | None = None

    if setting.credential_id is not None:
        cred = await credential_service.get_system(db, setting.credential_id)
        if cred is not None:
            credential_name = cred.name
            provider = cred.definition_key
            try:
                payload = await credential_service.decrypt_with_external(
                    cred.data_encrypted
                )
                raw = payload.get("base_url")
                base_url = str(raw) if raw else None
            except Exception:  # noqa: BLE001
                logger.exception(
                    "System LLM credential %s decryption failed", cred.id
                )

    configured = setting.credential_id is not None and bool(setting.model_name)
    return SystemLlmSettingOut(
        role=setting.role,
        credential_id=setting.credential_id,
        credential_name=credential_name,
        provider=provider,
        base_url=base_url,
        model_name=setting.model_name,
        configured=configured,
        updated_at=setting.updated_at,
    )


@router.get("", response_model=list[SystemLlmSettingOut])
async def list_system_llm_settings(
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_super_user),
) -> list[SystemLlmSettingOut]:
    """All role slots (text_primary / text_fallback / image). Super_user only."""
    result = await db.execute(select(SystemLlmSetting))
    by_role = {s.role: s for s in result.scalars().all()}
    out: list[SystemLlmSettingOut] = []
    for role in SYSTEM_LLM_ROLES:
        setting = by_role.get(role)
        if setting is None:
            # Seed missing (e.g. legacy DB) — self-heal so the screen renders.
            setting = SystemLlmSetting(role=role)
            db.add(setting)
            await db.flush()
        out.append(await _build_out(db, setting))
    return out


@router.put("/{role}", response_model=SystemLlmSettingOut)
async def update_system_llm_setting(
    role: str,
    payload: SystemLlmSettingUpdate,
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_super_user),
    _csrf: None = Depends(verify_csrf),
) -> SystemLlmSettingOut:
    """Select (or clear) the credential/model for a role. Super_user only."""
    if role not in SYSTEM_LLM_ROLES:
        raise HTTPException(status_code=404, detail="unknown system LLM role")

    if payload.credential_id is not None:
        cred = await credential_service.get_system(db, payload.credential_id)
        if cred is None:
            logger.info(
                "Rejected system LLM credential %s: not an existing system credential",
                payload.credential_id,
            )
            raise HTTPException(status_code=404, detail=_INVALID_CREDENTIAL_DETAIL)
        if cred.definition_key not in _LLM_DEFINITION_KEYS:
            logger.info(
                "Rejected system LLM credential %s: definition_key %r not an LLM provider",
                payload.credential_id,
                cred.definition_key,
            )
            raise HTTPException(status_code=422, detail=_INVALID_CREDENTIAL_DETAIL)

    result = await db.execute(
        select(SystemLlmSetting).where(SystemLlmSetting.role == role)
    )
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = SystemLlmSetting(role=role)
        db.add(setting)

    setting.credential_id = payload.credential_id
    setting.model_name = payload.model_name
    await db.commit()
    await db.refresh(setting)
    return await _build_out(db, setting)


__all__ = ["router"]
