"""Health check service — model + MCP server probes with history persistence.

Reuses the M8 ``model_test`` probe so a "Check now" button hits the same
code path as the user-facing test surface; an MCP probe wraps
``app.mcp.client.connect_and_list``. Each probe writes one
:class:`~app.models.health_check_history.HealthCheckHistory` row and updates
the target's "last status" columns when present.

The bucketed status column maps to a single coloured chip in the UI
(``healthy`` / ``degraded`` / ``unhealthy``) so the frontend doesn't need
provider-specific glue. ``degraded`` is reserved for "responded but not
ready" cases (MCP ``auth_needed``).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.credentials import service as credential_service
from app.mcp import client as mcp_client
from app.mcp.auth import resolve_mcp_auth
from app.models.credential import Credential
from app.models.health_check_history import HealthCheckHistory
from app.models.mcp_server import McpServer
from app.models.model import Model
from app.services import model_test

logger = logging.getLogger(__name__)


HealthStatus = Literal["healthy", "unhealthy", "degraded"]


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def check_model(
    db: AsyncSession,
    *,
    model: Model,
    credential: Credential | None,
) -> HealthCheckHistory:
    """Probe a single model row and append a history entry.

    ``credential`` may be ``None`` when the deployment relies on env-var
    fallback inside the model factory; in that case we still issue the probe
    with an empty payload so the provider's missing-key error path lights up
    a deterministic ``unhealthy`` status.
    """

    payload: dict[str, Any] = {}
    if credential is not None:
        try:
            payload = await credential_service.decrypt_with_external(credential.data_encrypted)
        except Exception as exc:  # noqa: BLE001 — surface as unhealthy
            return await _record(
                db,
                target_kind="model",
                target_id=model.id,
                status="unhealthy",
                latency_ms=None,
                error_kind="other",
                error_message=f"credential decryption failed: {exc}",
                raw={"phase": "decrypt"},
            )

    result = await model_test.run_model_test(
        provider=model.provider,
        model_name=model.model_name,
        base_url=model.base_url,
        credential_data=payload,
        cost_per_input_token=model.cost_per_input_token,
        cost_per_output_token=model.cost_per_output_token,
    )

    if result.success:
        return await _record(
            db,
            target_kind="model",
            target_id=model.id,
            status="healthy",
            latency_ms=result.latency_ms,
            error_kind=None,
            error_message=None,
            raw={"tokens_in": result.tokens_in, "tokens_out": result.tokens_out},
        )

    err = result.error
    return await _record(
        db,
        target_kind="model",
        target_id=model.id,
        status="unhealthy",
        latency_ms=result.latency_ms,
        error_kind=err.kind if err else "other",
        error_message=err.message if err else None,
        raw={"raw": (err.raw if err else None)},
    )


async def check_mcp_server(
    db: AsyncSession,
    *,
    server: McpServer,
    credential: Credential | None,
) -> HealthCheckHistory:
    """Probe a single MCP server row and append a history entry.

    Treats stdio servers as ``unhealthy`` with a documented error so the UI
    can warn users that their stdio config can't be probed remotely (the MCP
    SDK client used here is HTTP-only).
    """

    try:
        auth = await resolve_mcp_auth(
            db,
            credential_id=credential.id if credential is not None else server.credential_id,
            user_id=server.user_id,
            static_headers=server.headers,
        )
        if auth.error:
            status: HealthStatus = "degraded" if auth.status == "auth_needed" else "unhealthy"
            history = await _record(
                db,
                target_kind="mcp_server",
                target_id=server.id,
                status=status,
                latency_ms=None,
                error_kind="auth",
                error_message=f"credential resolution failed: {auth.error}",
                raw={"phase": "auth", "status": auth.status},
            )
            await _update_mcp_status(server, history)
            return history
    except Exception as exc:  # noqa: BLE001 — surface as unhealthy
        history = await _record(
            db,
            target_kind="mcp_server",
            target_id=server.id,
            status="unhealthy",
            latency_ms=None,
            error_kind="auth",
            error_message=f"credential resolution failed: {exc}",
            raw={"phase": "auth"},
        )
        await _update_mcp_status(server, history)
        return history

    started_at = datetime.now(UTC).replace(tzinfo=None)
    probe = await mcp_client.connect_and_list(
        transport=server.transport,
        url=server.url,
        headers=auth.headers,
        credentials=auth.credentials,
    )
    latency_ms = int((datetime.now(UTC).replace(tzinfo=None) - started_at).total_seconds() * 1000)

    if probe.get("success"):
        tools = probe.get("tools") or []
        history = await _record(
            db,
            target_kind="mcp_server",
            target_id=server.id,
            status="healthy",
            latency_ms=latency_ms,
            error_kind=None,
            error_message=None,
            raw={"tool_count": len(tools)},
        )
        server.last_pinged_at = history.checked_at
        server.last_tool_count = len(tools)
        server.last_error = None
        server.status = "connected"
        return history

    raw_err = str(probe.get("error") or "").strip()
    kind, status_label = _classify_mcp_error(raw_err)
    history = await _record(
        db,
        target_kind="mcp_server",
        target_id=server.id,
        status=status_label,
        latency_ms=latency_ms,
        error_kind=kind,
        error_message=raw_err or None,
        raw={"phase": "connect"},
    )
    await _update_mcp_status(server, history, error=raw_err)
    return history


async def check_all_active(
    db: AsyncSession,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, int]:
    """Probe every active model + MCP server. Used by the cron job.

    Returns counters so callers can log a one-line summary. Skips rows
    silently when their definition can't be resolved (mis-configured
    credential FK, etc.) — the per-row history capture covers the gap.
    """

    counters = {
        "models_checked": 0,
        "mcp_servers_checked": 0,
        "healthy": 0,
        "unhealthy": 0,
        "degraded": 0,
    }

    model_ids = list(
        (await db.execute(select(Model.id).order_by(Model.created_at.asc()))).scalars().all()
    )
    server_ids = list(
        (await db.execute(select(McpServer.id).where(McpServer.status != "disabled")))
        .scalars()
        .all()
    )

    if session_factory is None:
        for model_id in model_ids:
            model = await db.get(Model, model_id)
            if model is None:
                continue
            cred = await _resolve_model_credential(db, model)
            history = await check_model(db, model=model, credential=cred)
            counters["models_checked"] += 1
            counters[history.status] = counters.get(history.status, 0) + 1
            await db.commit()

        for server_id in server_ids:
            server = await db.get(McpServer, server_id)
            if server is None:
                continue
            cred = await _resolve_credential(db, server.credential_id)
            history = await check_mcp_server(db, server=server, credential=cred)
            counters["mcp_servers_checked"] += 1
            counters[history.status] = counters.get(history.status, 0) + 1
            await db.commit()
        logger.info("health check sweep finished: %s", counters)
        return counters

    semaphore = asyncio.Semaphore(max(1, settings.health_check_concurrency))
    counter_lock = asyncio.Lock()

    async def record_counter(kind: str, status: str) -> None:
        async with counter_lock:
            counters[kind] += 1
            counters[status] = counters.get(status, 0) + 1

    async def check_model_id(model_id: uuid.UUID) -> None:
        async with semaphore, session_factory() as session:
            model = await session.get(Model, model_id)
            if model is None:
                return
            cred = await _resolve_model_credential(session, model)
            history = await check_model(session, model=model, credential=cred)
            await session.commit()
            await record_counter("models_checked", history.status)

    async def check_server_id(server_id: uuid.UUID) -> None:
        async with semaphore, session_factory() as session:
            server = await session.get(McpServer, server_id)
            if server is None:
                return
            cred = await _resolve_credential(session, server.credential_id)
            history = await check_mcp_server(session, server=server, credential=cred)
            await session.commit()
            await record_counter("mcp_servers_checked", history.status)

    await asyncio.gather(
        *(check_model_id(model_id) for model_id in model_ids),
        *(check_server_id(server_id) for server_id in server_ids),
    )

    logger.info("health check sweep finished: %s", counters)
    return counters


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _record(
    db: AsyncSession,
    *,
    target_kind: str,
    target_id: uuid.UUID,
    status: HealthStatus,
    latency_ms: int | None,
    error_kind: str | None,
    error_message: str | None,
    raw: dict[str, Any] | None,
) -> HealthCheckHistory:
    history = HealthCheckHistory(
        target_kind=target_kind,
        target_id=target_id,
        status=status,
        latency_ms=latency_ms,
        error_kind=error_kind,
        error_message=(error_message or None),
        raw_result=raw,
        checked_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(history)
    await db.flush()
    return history


def _classify_mcp_error(raw: str) -> tuple[str, HealthStatus]:
    """Map an MCP probe error string to ``(error_kind, status)``."""

    lower = (raw or "").lower()
    if "auth" in lower or "401" in lower or "unauthorized" in lower or "403" in lower:
        return "auth", "degraded"
    if "not supported" in lower:
        return "other", "unhealthy"
    if "timeout" in lower or "timed out" in lower:
        return "timeout", "unhealthy"
    if "url is required" in lower:
        return "not_found", "unhealthy"
    return "other", "unhealthy"


async def _update_mcp_status(
    server: McpServer,
    history: HealthCheckHistory,
    *,
    error: str | None = None,
) -> None:
    server.last_pinged_at = history.checked_at
    if history.status == "degraded":
        server.status = "auth_needed"
    elif history.status == "unhealthy":
        server.status = "unreachable"
    server.last_error = error or history.error_message


async def _resolve_credential(
    db: AsyncSession, credential_id: uuid.UUID | None
) -> Credential | None:
    if credential_id is None:
        return None
    return await db.get(Credential, credential_id)


async def _resolve_model_credential(db: AsyncSession, model: Model) -> Credential | None:
    """Pick the first agent-bound credential for this model, if any."""

    from app.models.agent import Agent

    result = await db.execute(
        select(Credential)
        .join(Agent, Agent.llm_credential_id == Credential.id)
        .where(Agent.model_id == model.id)
        .limit(1)
    )
    return result.scalar_one_or_none()


__all__ = [
    "check_all_active",
    "check_mcp_server",
    "check_model",
]
