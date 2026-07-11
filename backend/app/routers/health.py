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

from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import (
    credential_not_found,
    mcp_server_not_found,
    model_not_found,
)
from app.models.credential import Credential
from app.models.health_check_history import HealthCheckHistory
from app.models.mcp_server import McpServer
from app.models.model import Model
from app.services import health_check as health_check_service
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
async def list_model_health(
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
    models = (await db.execute(select(Model))).scalars().all()
    latest = await _latest_for(db, "model", [m.id for m in models])
    return [_summary_from("model", m.id, m.display_name, latest.get(m.id)) for m in models]


@router.get("/mcp-servers", response_model=list[HealthSummaryEntry])
async def list_mcp_health(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    servers = (
        (await db.execute(select(McpServer).where(McpServer.user_id == user.id))).scalars().all()
    )
    latest = await _latest_for(db, "mcp_server", [s.id for s in servers])
    return [_summary_from("mcp_server", s.id, s.name, latest.get(s.id)) for s in servers]


@router.get("/history", response_model=list[HealthHistoryEntry])
async def get_history(
    target_kind: Literal["model", "mcp_server"] = Query(...),
    target_id: uuid.UUID = Query(...),
    limit: int = Query(30, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = Depends(get_current_user),
):
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
async def check_now(
    target_kind: Literal["model", "mcp_server"] = Query(...),
    target_id: uuid.UUID = Query(...),
    credential_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    """Run a probe immediately and return the resulting history row.

    For model targets, ``credential_id`` is optional — when omitted, the
    model's ``default_credential_id`` is used. This keeps the row [Check]
    button on the models page calling the credential the user picked at
    Add-model time, instead of a provider-matched fallback.
    """

    if target_kind == "model":
        model = await db.get(Model, target_id)
        if model is None:
            raise model_not_found()
        credential = await resolve_credential_for_model(db, model, credential_id, user.id)
        if credential is None:
            if credential_id is not None:
                raise HTTPException(status_code=404, detail="credential not found")
            raise HTTPException(
                status_code=422,
                detail=(
                    "no usable credential — pass credential_id or set the "
                    "model's default_credential_id."
                ),
            )
        history = await health_check_service.check_model(db, model=model, credential=credential)
    else:
        # MCP servers carry their own credential reference; fall back to
        # the manual lookup we used before.
        credential: Credential | None = None
        if credential_id is not None:
            credential = await db.get(Credential, credential_id)
            if credential is None or credential.user_id != user.id:
                raise credential_not_found()
        server = await db.get(McpServer, target_id)
        if server is None or server.user_id != user.id:
            raise mcp_server_not_found()
        history = await health_check_service.check_mcp_server(
            db, server=server, credential=credential
        )

    await db.commit()
    await db.refresh(history)
    return history


__all__ = ["router"]
