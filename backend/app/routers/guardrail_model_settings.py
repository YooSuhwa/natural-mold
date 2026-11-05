"""Guardrail model service settings (guardrail-minimum-plan.md §2.1) — operator only.

The operator turns the Korean model pack on or off, points it at a ``pii-detector`` service
and tests the address. Off, or a service that does not answer, means the Korean rules pack
checks instead; a guardrail check is never skipped. The address is internal by design
(Compose: ``http://pii-detector:8095``), so private addresses are allowed; only operators
can set or probe it, and the probe reports an outcome, never the response body.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.policies import SUPER, authz_policy
from app.config import settings
from app.dependencies import CurrentUser, get_db, require_platform_operator, verify_csrf
from app.exceptions import NotFoundError, ValidationError
from app.guardrails import model_settings
from app.schemas.guardrail_model_setting import (
    GuardrailModelProbeOut,
    GuardrailModelProbeRequest,
    GuardrailModelSettingOut,
    GuardrailModelSettingUpdate,
)
from app.services import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/guardrail-model-settings", tags=["guardrail-model-settings"])

# One message for a missing and a wrong-type credential (no enumeration of system credentials).
_INVALID_CREDENTIAL = "credential_id must reference a system HTTP Bearer credential"


def _host(url: str) -> str | None:
    return urlsplit(url).hostname if url else None


def _check_url(url: str, *, required: bool) -> None:
    if not url and not required:
        return
    reason = model_settings.invalid_url_reason(url) if url else "missing"
    if reason is not None:
        # The reason is a fixed word (missing, scheme_or_host, ...) the screen translates.
        raise ValidationError("GUARDRAIL_MODEL_INVALID_URL", reason)


async def _check_credential(db: AsyncSession, credential_id: uuid.UUID | None) -> None:
    if credential_id is not None and not await model_settings.is_bearer_credential(
        db, credential_id
    ):
        raise NotFoundError("GUARDRAIL_MODEL_CREDENTIAL_NOT_FOUND", _INVALID_CREDENTIAL)


async def _out(db: AsyncSession) -> GuardrailModelSettingOut:
    view = await model_settings.view(db)
    return GuardrailModelSettingOut(
        enabled=view.enabled,
        url=view.url,
        source=view.source,
        environment_url=view.environment_url,
        credential_id=view.credential_id,
        credential_name=view.credential_name,
        updated_at=view.updated_at,
    )


async def _audit(
    db: AsyncSession,
    request: Request,
    user: CurrentUser,
    action: str,
    outcome: str,
    metadata: dict[str, Any],
) -> None:
    await audit_service.record_event(
        db,
        actor_type="user",
        actor_user_id=user.id,
        actor_email_snapshot=user.email,
        owner_user_id=user.id,
        owner_email_snapshot=user.email,
        action=action,
        target_type="guardrail_model_setting",
        target_id="kr_model",
        target_name_snapshot="kr_model",
        target_owner_user_id=None,
        outcome=outcome,
        request=request,
        metadata=metadata,
    )
    await db.commit()


def _snapshot(out: GuardrailModelSettingOut) -> dict[str, Any]:
    return {
        "source": out.source,
        "enabled": out.enabled,
        "url": out.url,
        "credential_bound": out.credential_id is not None,
    }


@router.get("", response_model=GuardrailModelSettingOut)
@authz_policy(SUPER)
async def get_guardrail_model_setting(
    db: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_platform_operator),
) -> GuardrailModelSettingOut:
    return await _out(db)


@router.put("", response_model=GuardrailModelSettingOut)
@authz_policy(SUPER)
async def update_guardrail_model_setting(
    payload: GuardrailModelSettingUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_platform_operator),
    _csrf: None = Depends(verify_csrf),
) -> GuardrailModelSettingOut:
    url = payload.url.strip()
    _check_url(url, required=payload.enabled)
    await _check_credential(db, payload.credential_id)
    before = _snapshot(await _out(db))
    await model_settings.save(
        db, enabled=payload.enabled, url=url, credential_id=payload.credential_id, user_id=user.id
    )
    out = await _out(db)
    await _audit(
        db,
        request,
        user,
        "guardrail_model_setting.update",
        "success",
        {"before": before, "after": _snapshot(out)},
    )
    return out


@router.delete("", status_code=204)
@authz_policy(SUPER)
async def reset_guardrail_model_setting(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_platform_operator),
    _csrf: None = Depends(verify_csrf),
) -> Response:
    """Forget the saved setting; the environment applies again."""
    before = _snapshot(await _out(db))
    if await model_settings.forget(db):
        after = _snapshot(await _out(db))
        await _audit(
            db,
            request,
            user,
            "guardrail_model_setting.reset",
            "success",
            {"before": before, "after": after},
        )
    return Response(status_code=204)


@router.post("/test", response_model=GuardrailModelProbeOut)
@authz_policy(SUPER)
async def test_guardrail_model_setting(
    payload: GuardrailModelProbeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_platform_operator),
    _csrf: None = Depends(verify_csrf),
) -> GuardrailModelProbeOut:
    url = payload.url.strip()
    _check_url(url, required=True)
    await _check_credential(db, payload.credential_id)
    api_key = settings.guardrail_pii_model_api_key
    if payload.credential_id is not None:
        api_key = await model_settings.bearer_token(db, payload.credential_id) or ""
    result = await model_settings.probe(url, api_key)
    await _audit(
        db,
        request,
        user,
        "guardrail_model_setting.test",
        "success" if result.status == "ok" else "failure",
        {
            "status": result.status,
            "host": _host(url),
            "credential_bound": bool(payload.credential_id),
        },
    )
    return GuardrailModelProbeOut(
        status=result.status,
        latency_ms=result.latency_ms,
        model=result.model,
        findings=result.findings,
    )


__all__ = ["router"]
