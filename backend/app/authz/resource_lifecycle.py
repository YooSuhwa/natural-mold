"""Resource lifecycle -> OpenFGA tuples (plan 7.2.1 "자원 생성", "소유권 이전", "자원 삭제").

Callers hook the ORM row in their own transaction and apply after commit::

    change = await on_resource_created(db, agent, mode=authz.mode)
    await db.commit()
    await apply_lifecycle(change, authz)  # 8.6 item 2: before responding (read-your-writes)

* ``legacy``: every hook returns an empty change without touching the session, so the
  request's queries stay byte-identical.
* ``shadow``/``enforce``: the row's 7.2 tuples (``tuples_for``) are queued in
  ``authz_outbox`` (C-04 ``enqueue``) in the caller's transaction; ``apply_lifecycle``
  writes them at once (2 s budget, best effort) and the C-05 drain retries the rest.

Deletes and ownership transfers follow 8.6: a ``resource``/``ownership`` revocation
marker is recorded with the queued deletes (the deny overlay refuses the object until
they land) and is finalized by the outbox completion. Deleting a resource also retires
the active grants on it (and, for an agent, the grants naming it as principal) as
``revoked`` and deletes their tuples. Platform (``is_system``) rows project no
org/owner tuples; their organization fanout belongs to the catalog/bootstrap jobs.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import tuple_rows as rows
from app.authz.grant_completion import MARKERS_TABLE
from app.authz.grant_projection import written_tuples
from app.authz.outbox import OutboxRun, Sessions, TupleOp, TupleWriter, apply_now, enqueue
from app.authz.revocations import ANY_SUBJECT, record_revocation
from app.authz.service_core import AuthzDecisions
from app.authz.service_types import AuthzMode
from app.authz.tuple_loaders import grant_row
from app.authz.tuple_types import RelationTuple
from app.authz.tuples import tuples_for
from app.config import settings
from app.database import platform_sessions
from app.models.agent import Agent
from app.models.authz import ResourceGrant
from app.models.credential import Credential
from app.models.marketplace import MarketplaceItem
from app.models.mcp_server import McpServer
from app.models.mcp_tool import McpTool
from app.models.skill import Skill
from app.models.tool import Tool

LifecycleRow = Agent | Skill | Tool | McpServer | Credential | MarketplaceItem | McpTool
_RESOURCE_TYPES: dict[type, str] = {
    Agent: "agent",
    Skill: "skill",
    Tool: "tool",
    McpServer: "mcp_server",
    Credential: "credential",
    MarketplaceItem: "marketplace_item",
    McpTool: "mcp_tool",
}


@dataclass(frozen=True, slots=True)
class LifecycleChange:
    """Outbox rows queued by one hook; empty in legacy mode."""

    outbox_ids: tuple[UUID, ...] = ()


def resource_type_of(row: LifecycleRow) -> str:
    return _RESOURCE_TYPES[type(row)]


def source_row(row: LifecycleRow) -> rows.Row:
    """The 7.2 snapshot of one ORM row (same columns as ``tuple_snapshot``)."""
    match row:
        case Agent():
            return rows.ResourceRow(
                "agent", row.id, row.org_id, row.user_id, archived_at=row.archived_at
            )
        case Skill() | Tool() | McpServer():
            kind = resource_type_of(row)
            return rows.ResourceRow(kind, row.id, row.org_id, row.user_id, bool(row.is_system))
        case Credential():
            return rows.CredentialRow(
                row.id, row.org_id, row.tenant_id, row.user_id, row.scope, bool(row.is_system)
            )
        case MarketplaceItem():
            return rows.MarketplaceItemRow(
                row.id,
                row.org_id,
                row.tenant_id,
                row.owner_user_id,
                bool(row.is_system),
                row.tenant_publication or "none",
            )
        case McpTool():
            return rows.McpToolRow(row.id, row.server_id)


def _active(mode: AuthzMode | None) -> bool:
    return (mode or settings.authz_mode) != "legacy"


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


async def _ensure_id(db: AsyncSession, row: LifecycleRow) -> UUID:
    if row.id is None:  # python-side uuid default is assigned at flush
        await db.flush()
    return row.id


async def on_resource_created(
    db: AsyncSession,
    row: LifecycleRow,
    *,
    mode: AuthzMode | None = None,
    now: datetime | None = None,
) -> LifecycleChange:
    """Queue the new row's org/owner (resident, tenant, server) tuples."""
    if not _active(mode):
        return LifecycleChange()
    stamp = _now(now)
    rid = await _ensure_id(db, row)
    ops = [TupleOp("write", item) for item in tuples_for(source_row(row), now=stamp)]
    ids = await enqueue(db, ops, source_table=row.__tablename__, source_id=rid, now=stamp)
    return LifecycleChange(tuple(ids))


async def on_owner_changed(
    db: AsyncSession,
    row: LifecycleRow,
    *,
    previous_owner_id: UUID | None,
    mode: AuthzMode | None = None,
    now: datetime | None = None,
) -> LifecycleChange:
    """Call after assigning the new owner column; swaps the ``owner`` tuple (8.6 marker)."""
    if not _active(mode):
        return LifecycleChange()
    stamp = _now(now)
    after = source_row(row)
    if isinstance(after, rows.McpToolRow):
        raise TypeError("mcp_tool rows have no owner")
    before = replace(after, owner_id=previous_owner_id)
    old, new = set(tuples_for(before, now=stamp)), set(tuples_for(after, now=stamp))
    ops = [TupleOp("delete", t) for t in _sorted(old - new)]
    ops += [TupleOp("write", t) for t in _sorted(new - old)]
    subject = f"user:{previous_owner_id}" if previous_owner_id else ANY_SUBJECT
    return await _queue(db, row, ops, scope="ownership", subject=subject, now=stamp)


async def on_resource_deleted(
    db: AsyncSession,
    row: LifecycleRow,
    *,
    mode: AuthzMode | None = None,
    now: datetime | None = None,
) -> LifecycleChange:
    """Call before (or right after) ``db.delete(row)``, in the same transaction."""
    if not _active(mode):
        return LifecycleChange()
    stamp = _now(now)
    found = list(tuples_for(source_row(row), now=stamp))
    if isinstance(row, McpServer):
        tools = await db.scalars(select(McpTool).where(McpTool.server_id == row.id))
        found += [t for tool in tools for t in tuples_for(source_row(tool), now=stamp)]
    for grant in await _grants_of(db, resource_type_of(row), row.id):
        found += written_tuples(grant_row(grant))
        grant.state, grant.revoked_at = "revoked", stamp
    ops = [TupleOp("delete", t) for t in _sorted(set(found))]
    return await _queue(db, row, ops, scope="resource", subject=ANY_SUBJECT, now=stamp)


async def _grants_of(db: AsyncSession, kind: str, rid: UUID) -> list[ResourceGrant]:
    on_object = (ResourceGrant.resource_type == kind) & (ResourceGrant.resource_id == rid)
    clauses: list[Any] = [on_object]
    if kind == "agent":  # delegations by the agent (7.2.1: "그 에이전트가 주체인 튜플")
        clauses.append(
            (ResourceGrant.principal_type == "agent") & (ResourceGrant.principal_id == rid)
        )
    query = select(ResourceGrant).where(ResourceGrant.state == "active", or_(*clauses))
    return list(await db.scalars(query.order_by(ResourceGrant.id)))


async def _queue(
    db: AsyncSession,
    row: LifecycleRow,
    ops: list[TupleOp],
    *,
    scope: Literal["ownership", "resource"],
    subject: str,
    now: datetime,
) -> LifecycleChange:
    org_id, tenant_id = getattr(row, "org_id", None), getattr(row, "tenant_id", None)
    if not ops:
        return LifecycleChange()
    if org_id is None or tenant_id is None:  # platform/company rows: no org-scoped marker
        ids = await enqueue(db, ops, source_table=row.__tablename__, source_id=row.id, now=now)
        return LifecycleChange(tuple(ids))
    obj = f"{resource_type_of(row)}:{row.id}"
    marker = await record_revocation(
        db, org_id=org_id, tenant_id=tenant_id, scope=scope, subject=subject, object=obj
    )
    ids = await enqueue(db, ops, source_table=MARKERS_TABLE, source_id=marker.id, now=now)
    return LifecycleChange(tuple(ids))


def _sorted(items: Iterable[RelationTuple]) -> list[RelationTuple]:
    return sorted(items, key=lambda t: t.sort_key)


async def apply_lifecycle(
    change: LifecycleChange,
    authz: AuthzDecisions,
    *,
    writer: TupleWriter | None = None,
    sessions: Sessions | None = None,
) -> OutboxRun:
    """After commit (8.6 item 2): write the queued rows now; never raises."""
    writer = writer or authz.tuple_writer
    if not change.outbox_ids or writer is None:
        return OutboxRun()
    sessions = sessions or platform_sessions("authz.outbox_apply")
    return await apply_now(sessions, writer, change.outbox_ids)
