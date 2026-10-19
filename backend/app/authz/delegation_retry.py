"""Re-validate delegations once revocations settle (plan 8.6 위임 검증, 10.6; F-03).

The event-time validator defers what is not settled yet (pending markers, undelivered
outbox rows) and gives up on ``AuthzUnavailable``. Every revocation records a marker
that is finalized only when OpenFGA confirmed the deletes, so the C-05 maintenance tick
polls markers finalized since its watermark and re-validates their scope per
organization: the object (``tool:…`` …, MCP tools by server) or, for organization-wide
markers, the subject user as delegator. An organization whose run failed or still
deferred keeps the watermark at its markers (bounded by ``HOLD``; older ones are left to
the daily sweep). The watermark lives in the leader process: a new leader starts
``LOOKBACK`` back (validation is idempotent).
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.agent_delegations import LINK_SOURCES, Key
from app.authz.delegation_links import DelegationScope
from app.authz.delegation_sweep import Maker, validate_in_session
from app.authz.revocations import ORG_WIDE_SCOPES
from app.authz.service_core import AuthzDecisions
from app.database import platform_session
from app.models.authz import AuthzRevocation
from app.models.mcp_tool import McpTool

logger = logging.getLogger(__name__)
LOOKBACK = timedelta(minutes=15)
HOLD = timedelta(hours=1)
TRIGGER = "revocation_finalized"
_state: dict[str, datetime] = {}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def revalidate_finalized(
    authz: AuthzDecisions, *, now: datetime | None = None, maker: Maker | None = None
) -> dict[UUID, bool]:
    """One poll; returns per organization whether its scope settled (no failure/defer)."""
    if authz.mode == "legacy":
        return {}
    stamp = now or datetime.now(UTC)
    since = _state.get("watermark", stamp - LOOKBACK)
    listing = (
        platform_session("authz.delegation_retry")
        if maker is None
        else platform_session("authz.delegation_retry", maker=maker)
    )
    async with listing as db:
        rows = (
            await db.execute(
                select(
                    AuthzRevocation.org_id,
                    AuthzRevocation.tenant_id,
                    AuthzRevocation.scope,
                    AuthzRevocation.subject,
                    AuthzRevocation.object,
                    AuthzRevocation.finalized_at,
                )
                .where(AuthzRevocation.finalized_at > since, AuthzRevocation.finalized_at <= stamp)
                .order_by(AuthzRevocation.finalized_at, AuthzRevocation.id)
            )
        ).all()
        servers = await _servers(db, {r[4] for r in rows})
    scopes: dict[UUID, tuple[UUID, set[Key], set[UUID], datetime]] = {}
    for org_id, tenant_id, scope, subject, obj, finalized in rows:
        _, objects, delegators, first = scopes.setdefault(
            org_id, (tenant_id, set(), set(), _aware(finalized))
        )
        if (key := _key(obj, servers)) is not None:
            objects.add(key)
        if scope in ORG_WIDE_SCOPES and subject.startswith("user:"):
            delegators.add(UUID(subject.removeprefix("user:")))
    settled: dict[UUID, bool] = {}
    hold = stamp
    for org_id, (tenant_id, objects, delegators, first) in sorted(scopes.items()):
        if not objects and not delegators:
            continue
        scope = DelegationScope(org_id, frozenset(objects), frozenset(delegators))
        try:
            report = await validate_in_session(
                authz, scope, tenant_id, trigger=TRIGGER, maker=maker
            )
            settled[org_id] = not (report.deferred or report.failed)
        except Exception:  # noqa: BLE001 - AuthzUnavailable etc.: next tick retries
            logger.exception("delegation_retry_failed", extra={"org_id": str(org_id)})
            settled[org_id] = False
        if not settled[org_id] and stamp - first < HOLD:
            hold = min(hold, first - timedelta(microseconds=1))
    _state["watermark"] = hold
    return settled


def _key(obj: str, servers: dict[UUID, UUID]) -> Key | None:
    kind, _, raw = obj.partition(":")
    try:
        rid = UUID(raw)
    except ValueError:
        return None
    if kind == "mcp_tool":
        server = servers.get(rid)
        return None if server is None else ("mcp_server", server)
    return (kind, rid) if kind in LINK_SOURCES else None


async def _servers(db: AsyncSession, objects: set[str]) -> dict[UUID, UUID]:
    ids = []
    for obj in objects:
        kind, _, raw = obj.partition(":")
        if kind == "mcp_tool":
            try:
                ids.append(UUID(raw))
            except ValueError:
                continue
    if not ids:
        return {}
    found = await db.execute(select(McpTool.id, McpTool.server_id).where(McpTool.id.in_(ids)))
    return {row[0]: row[1] for row in found.all()}
