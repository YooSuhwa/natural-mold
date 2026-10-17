"""Lightweight MCP health polling (BE-S9), one company per session (I-06).

Moved out of ``mcp_service`` (re-exported there). The scheduler lists the
companies that own pollable servers in platform scope, then polls each
company's servers in that company's session; platform catalog servers
(``tenant_id IS NULL``, writable only in platform scope) are polled in the
platform session.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.authz.mcp_probe_scope import require_mcp_probe_scope
from app.exceptions import AppError
from app.mcp import discovery as mcp_discovery
from app.models.mcp_server import McpServer

logger = logging.getLogger("app.services.mcp_service")
_POLLABLE = or_(McpServer.is_system.is_(True), McpServer.status != "disabled")


async def poll_mcp_servers_health(
    *,
    session_factory: Callable[[], AsyncSession],
    company: ColumnElement[bool] | None = None,
) -> dict[str, int]:
    """Run a quick connectivity probe against every enabled MCP server.

    Distinct from ``health_check_all_active`` (which writes a persistent
    history row): this job only refreshes the lightweight
    ``health_status`` / ``health_polled_at`` / ``health_message`` columns
    so the list view can show a fresh dot without paying for a full sweep.
    ``company`` narrows the sweep to one company's (or the catalog's) rows.
    """

    counters = {"checked": 0, "ok": 0, "error": 0}
    polled_at = datetime.now(UTC).replace(tzinfo=None)

    async with session_factory() as db:
        query = select(McpServer).where(_POLLABLE)
        rows = (await db.execute(query if company is None else query.where(company))).scalars()

        for server in rows.all():
            try:
                await require_mcp_probe_scope(db, server, server.credential_id)
            except AppError:
                continue
            counters["checked"] += 1
            try:
                probe = await mcp_discovery.test_server(db, server)
            except Exception as exc:  # noqa: BLE001 — keep the sweep alive
                logger.exception("mcp health poll failed for server %s", server.id)
                server.health_status = "error"
                server.health_polled_at = polled_at
                server.health_message = str(exc)
                counters["error"] += 1
                continue

            server.health_polled_at = polled_at
            if probe.get("success"):
                server.health_status = "ok"
                server.health_message = None
                counters["ok"] += 1
            else:
                server.health_status = "error"
                server.health_message = probe.get("error")
                counters["error"] += 1

        await db.commit()

    logger.info("mcp health poll finished: %s", counters)
    return counters


async def poll_mcp_health_by_company(
    *,
    platform: Callable[[], AsyncSession],
    company_sessions: Callable[[UUID], Callable[[], AsyncSession]],
) -> dict[str, int]:
    """Catalog servers in ``platform``, each company's servers in its own session.

    A failing company (or the catalog) is logged and skipped; the others still run.
    """
    async with platform() as db:
        tenants = (
            await db.scalars(
                select(McpServer.tenant_id)
                .where(_POLLABLE, McpServer.tenant_id.is_not(None))
                .distinct()
            )
        ).all()
    totals = {"checked": 0, "ok": 0, "error": 0}
    try:
        totals = await poll_mcp_servers_health(
            session_factory=platform, company=McpServer.tenant_id.is_(None)
        )
    except Exception:  # noqa: BLE001 — the catalog never blocks the companies
        logger.exception("mcp health poll failed for platform catalog servers")
    for tenant_id in sorted(t for t in tenants if t is not None):
        try:
            counters = await poll_mcp_servers_health(
                session_factory=company_sessions(tenant_id),
                company=McpServer.tenant_id == tenant_id,
            )
        except Exception:  # noqa: BLE001 — isolate companies
            logger.exception("mcp health poll failed for company %s", tenant_id)
            continue
        totals = {name: totals[name] + counters[name] for name in totals}
    return totals
