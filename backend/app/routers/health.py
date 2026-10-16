"""Health check API — latest status + per-target history + on-demand probe.

Routes:

- ``GET  /api/health/models``                 latest probe per registered model
- ``GET  /api/health/mcp-servers``            latest probe per MCP server
- ``GET  /api/health/history``                last N probes for a target
- ``POST /api/health/check``                  run a probe synchronously
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.company_catalog import model_scope
from app.authz.model_access import load_model_target
from app.authz.org_context import OrgContext
from app.authz.policies import CANDIDATES, RES, authz_policy
from app.authz.resource_gate import (
    CREDENTIAL,
    gate_list,
    gate_row,
    org_context_when_consulted,
    require_same_org,
)
from app.authz.runtime import get_authz
from app.authz.service import AuthzService
from app.credentials import service as credential_service
from app.credentials.validation import require_credential_use
from app.dependencies import CurrentUser, get_current_user, get_db, get_org_context, verify_csrf
from app.error_codes import (
    credential_not_found,
)
from app.models.credential import Credential
from app.models.health_check_history import HealthCheckHistory
from app.models.model import Model
from app.services import health_check as health_check_service
from app.services import mcp_service
from app.services.credential_resolver import resolve_credential_for_model

router = APIRouter(prefix="/api/health", tags=["health"])


# ---------------------------------------------------------------------------
# Schemas (router-local — small enough to keep next to the endpoints)
# ---------------------------------------------------------------------------


class HealthHistoryEntry(BaseModel):
    id: uuid.UUID
    target_kind: str
    target_id: uuid.UUID
    status: str
    latency_ms: int | None
    error_kind: str | None
    error_message: str | None
    checked_at: Any

    model_config = {"from_attributes": True}


class HealthSummaryEntry(BaseModel):
    """Latest status snapshot for a single target."""

    target_kind: str
    target_id: uuid.UUID
    name: str
    status: str | None = None
    latency_ms: int | None = None
    error_kind: str | None = None
    error_message: str | None = None
    checked_at: Any | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _latest_for(
    db: AsyncSession, target_kind: str, target_ids: list[uuid.UUID]
) -> dict[uuid.UUID, HealthCheckHistory]:
    """Return the most recent ``HealthCheckHistory`` per ``target_id``.

    Uses SQL windowing so the DB returns one row per target instead of
    loading every history row and reducing in Python.
    """

    if not target_ids:
        return {}
    ranked = (
        select(
            HealthCheckHistory.id.label("id"),
            func.row_number()
            .over(
                partition_by=HealthCheckHistory.target_id,
                order_by=HealthCheckHistory.checked_at.desc(),
            )
            .label("rn"),
        )
        .where(
            HealthCheckHistory.target_kind == target_kind,
            HealthCheckHistory.target_id.in_(target_ids),
        )
        .subquery()
    )
    rows = (
        (
            await db.execute(
                select(HealthCheckHistory)
                .join(ranked, HealthCheckHistory.id == ranked.c.id)
                .where(ranked.c.rn == 1)
            )
        )
        .scalars()
        .all()
    )
    return {row.target_id: row for row in rows}


def _summary_from(
    target_kind: str,
    target_id: uuid.UUID,
    name: str,
    history: HealthCheckHistory | None,
) -> HealthSummaryEntry:
    if history is None:
        return HealthSummaryEntry(
            target_kind=target_kind,
            target_id=target_id,
            name=name,
        )
    return HealthSummaryEntry(
        target_kind=target_kind,
        target_id=target_id,
        name=name,
        status=history.status,
        latency_ms=history.latency_ms,
        error_kind=history.error_kind,
        error_message=history.error_message,
        checked_at=history.checked_at,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/models", response_model=list[HealthSummaryEntry])
@authz_policy(CANDIDATES("llm_model", "can_use"))
async def list_model_health(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
    context: OrgContext | None = Depends(org_context_when_consulted),
    authz: AuthzService = Depends(get_authz),
):
    company = context.tenant_id if context is not None else None  # E-07: never another's
    models = (await db.execute(select(Model).where(model_scope(company)))).scalars().all()
    if context is not None:
        models = await gate_list(authz, context, "llm_model", "can_use", models, lambda m: m.id)
    latest = await _latest_for(db, "model", [m.id for m in models])
    return [_summary_from("model", m.id, m.display_name, latest.get(m.id)) for m in models]


@router.get("/mcp-servers", response_model=list[HealthSummaryEntry])
@authz_policy(CANDIDATES("mcp_server", "can_view"))
async def list_mcp_health(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
):
    servers = await mcp_service.list_servers(db, context, authz=authz, permission="can_view")
    latest = await _latest_for(db, "mcp_server", [s.id for s in servers])
    return [_summary_from("mcp_server", s.id, s.name, latest.get(s.id)) for s in servers]


@router.get("/history", response_model=list[HealthHistoryEntry])
@authz_policy(RES("{target_kind}", "can_view", param="target_id", min_visible="can_use"))
async def get_history(
    target_kind: Literal["model", "mcp_server"] = Query(...),
    target_id: uuid.UUID = Query(...),
    limit: int = Query(30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    authz: AuthzService = Depends(get_authz),
):
    if target_kind == "mcp_server":
        await mcp_service.load_owned(db, target_id, context, authz=authz)
    else:  # the platform catalog row is the legacy answer (D-10)
        await load_model_target(db, authz, context, target_id, user)
    return (
        (
            await db.execute(
                select(HealthCheckHistory)
                .where(
                    HealthCheckHistory.target_kind == target_kind,
                    HealthCheckHistory.target_id == target_id,
                )
                .order_by(HealthCheckHistory.checked_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


@router.post("/check", response_model=HealthHistoryEntry)
@authz_policy(RES("{target_kind}", "can_edit", param="target_id", min_visible="can_use"))
async def check_now(
    target_kind: Literal["model", "mcp_server"] = Query(...),
    target_id: uuid.UUID = Query(...),
    credential_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    context: OrgContext = Depends(get_org_context),
    _csrf: None = Depends(verify_csrf),
    authz: AuthzService = Depends(get_authz),
):
    """Run a probe immediately and return the resulting history row.

    For model targets, ``credential_id`` is optional — when omitted, the
    model's ``default_credential_id`` is used. This keeps the row [Check]
    button on the models page calling the credential the user picked at
    Add-model time, instead of a provider-matched fallback.
    """

    if target_kind == "model":
        model = await load_model_target(db, authz, context, target_id, user)
        if credential_id is not None:  # same owner lookup and 404 as the resolver's explicit tier
            credential = await require_credential_use(
                db, credential_id=credential_id, context=context, authz=authz
            )
        else:
            credential = await resolve_credential_for_model(db, model, None, context)
        if credential is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "no usable credential — pass credential_id or set the "
                    "model's default_credential_id."
                ),
            )
        require_same_org(authz, credential, context.org_id)  # platform model: caller's org
        history = await health_check_service.check_model(db, model=model, credential=credential)
    else:
        # MCP servers carry their own credential reference; fall back to
        # the manual lookup we used before.
        server = await mcp_service.load_owned(
            db, target_id, context, authz=authz, permission="can_edit"
        )
        credential: Credential | None = None
        selected_credential_id = credential_id or server.credential_id
        if selected_credential_id is not None:
            chosen = selected_credential_id

            async def legacy_credential() -> Credential:
                row = await credential_service.get_for_user(db, chosen, context)
                if row is None:
                    raise credential_not_found()
                return row

            credential = await gate_row(
                authz, db, context, CREDENTIAL, chosen, "can_use", legacy_credential
            )
            require_same_org(authz, credential, server.org_id)
        history = await health_check_service.check_mcp_server(
            db, server=server, credential=credential
        )

    await db.commit()
    await db.refresh(history)
    return history


__all__ = ["router"]
