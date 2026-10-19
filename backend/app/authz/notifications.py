"""Write side of in-app notifications (plan 14.2; H-02).

``notify`` writes one row per recipient in the caller's session (it commits with the
change that caused it). Rules of 14.2:

* a row stores the kind, the target (type, id), the actor and the target name at write
  time (``payload.target_name``) plus small enum fields — never a content body;
* the same kind + target + recipient (+ org, company) within the merge window (1 hour;
  24 hours for ``connection.owner_needed``, S22 "같은 항목 24시간 1회") is merged into
  the existing row: payload/actor/link refreshed, ``created_at`` and ``read_at`` kept, so
  the window is fixed from the first row and a merge never re-opens a read row;
* concurrent merges for one key are serialized by a transaction advisory lock on
  PostgreSQL (SQLite serializes writers itself);
* rows older than 90 days are pruned for a recipient at most once a day per process
  (reads hide them too).

The actor never notifies themselves. ``resource_managers`` lists the ``can_share``
holders of a resource (owner + active ``manager`` grants to users and groups, active
organization members only; ``mode`` legacy: the owner).
"""

import hashlib
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import and_, delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_administrators import active_org_users
from app.authz.share_targets import SHARE_TYPES
from app.models.authz import ResourceGrant
from app.models.notification import Notification
from app.models.organization import Organization, OrganizationMember
from app.models.organization_group import GroupMember

MERGE_WINDOW: Final = timedelta(hours=1)
WINDOWS: Final = {"connection.owner_needed": timedelta(hours=24)}
RETENTION: Final = timedelta(days=90)
PayloadValue = str | int | bool | None
_LINKS: Final = {
    "agent": "/agents/{id}",
    "skill": "/skills/{id}",
    "tool": "/tools",
    "mcp_server": "/mcp-servers",
    "credential": "/credentials",
}


@dataclass(frozen=True, slots=True)
class Target:
    type: str
    id: UUID
    name: str | None = None


def now() -> datetime:
    return datetime.now(UTC)


def resource_link(resource_type: str, resource_id: UUID) -> str | None:
    pattern = _LINKS.get(resource_type)
    return None if pattern is None else pattern.format(id=resource_id)


async def resource_target(db: AsyncSession, resource_type: str, resource_id: UUID) -> Target:
    kind = SHARE_TYPES.get(resource_type)
    name = None
    if kind is not None:
        name = await db.scalar(select(kind.model.name).where(kind.model.id == resource_id))
    return Target(resource_type, resource_id, name)


async def resource_managers(
    db: AsyncSession, org_id: UUID, resource_type: str, resource_id: UUID, *, mode: str
) -> set[UUID]:
    kind = SHARE_TYPES.get(resource_type)
    organization = await db.get(Organization, org_id)
    if kind is None or organization is None:
        return set()
    owner = await db.scalar(select(kind.model.user_id).where(kind.model.id == resource_id))
    found = {owner} if owner is not None else set()
    if mode != "legacy":
        stamp = now()
        grants = (
            await db.execute(
                select(ResourceGrant.principal_type, ResourceGrant.principal_id).where(
                    ResourceGrant.org_id == org_id,
                    ResourceGrant.resource_type == resource_type,
                    ResourceGrant.resource_id == resource_id,
                    ResourceGrant.relation == "manager",
                    ResourceGrant.state == "active",
                    or_(ResourceGrant.expires_at.is_(None), ResourceGrant.expires_at > stamp),
                )
            )
        ).all()
        found |= {pid for ptype, pid in grants if ptype == "user"}
        groups = {pid for ptype, pid in grants if ptype == "group"}
        if groups:
            members = select(GroupMember.user_id).where(
                GroupMember.group_id.in_(groups), GroupMember.org_id == org_id
            )
            found |= set(await db.scalars(members))
    if not found:
        return set()
    active = active_org_users(organization).where(OrganizationMember.user_id.in_(found))
    return set(await db.scalars(active))


_PRUNED: OrderedDict[UUID, datetime] = OrderedDict()
_PRUNED_LIMIT = 10_000
PRUNE_EVERY: Final = timedelta(days=1)


async def _prune(db: AsyncSession, user_id: UUID, stamp: datetime) -> None:
    last = _PRUNED.get(user_id)
    if last is not None and stamp - last < PRUNE_EVERY:
        return
    await db.execute(
        delete(Notification)
        .where(Notification.user_id == user_id, Notification.created_at < stamp - RETENTION)
        .execution_options(synchronize_session=False)
    )
    _PRUNED[user_id] = stamp
    _PRUNED.move_to_end(user_id)
    while len(_PRUNED) > _PRUNED_LIMIT:
        _PRUNED.popitem(last=False)


async def _lock_key(db: AsyncSession, *parts: object) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    key = int.from_bytes(digest[:8], "big", signed=True)
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


async def _merge_target(
    db: AsyncSession,
    user_id: UUID,
    scope: tuple[UUID, UUID | None],
    kind: str,
    target: Target | None,
    since: datetime,
) -> Notification | None:
    tenant_id, org_id = scope
    same_target = (
        and_(Notification.target_type.is_(None), Notification.target_id.is_(None))
        if target is None
        else and_(Notification.target_type == target.type, Notification.target_id == target.id)
    )
    return await db.scalar(
        select(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.tenant_id == tenant_id,
            Notification.org_id.is_not_distinct_from(org_id),
            Notification.kind == kind,
            same_target,
            Notification.created_at >= since,
        )
        .order_by(Notification.created_at.desc())
        .limit(1)
    )


async def notify(
    db: AsyncSession,
    *,
    kind: str,
    recipients: Iterable[UUID],
    tenant_id: UUID,
    org_id: UUID | None,
    target: Target | None,
    actor_id: UUID | None = None,
    payload: Mapping[str, PayloadValue] | None = None,
    link: str | None = None,
) -> list[Notification]:
    stamp = now()
    body: dict[str, PayloadValue] = {"target_name": target.name if target else None}
    body.update(payload or {})
    written: list[Notification] = []
    for user_id in dict.fromkeys(recipients):
        if user_id == actor_id:
            continue
        await _prune(db, user_id, stamp)
        await _lock_key(db, user_id, kind, target and target.id, tenant_id, org_id)
        window = WINDOWS.get(kind, MERGE_WINDOW)
        row = await _merge_target(db, user_id, (tenant_id, org_id), kind, target, stamp - window)
        if row is None:
            row = Notification(
                user_id=user_id,
                kind=kind,
                target_type=target.type if target else None,
                target_id=target.id if target else None,
                org_id=org_id,
                tenant_id=tenant_id,
                created_at=stamp,
            )
            db.add(row)
        row.actor_id, row.payload, row.link = actor_id, dict(body), link
        row.updated_at = stamp
        written.append(row)
    await db.flush()
    return written


__all__ = [
    "RETENTION",
    "Target",
    "notify",
    "resource_link",
    "resource_managers",
    "resource_target",
]
