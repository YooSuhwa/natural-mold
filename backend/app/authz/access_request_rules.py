"""10.9/S18 rules for access requests (H-01): levels, limits, lazy expiry.

* Requestable: a level of the type up to ``editor`` ("관리" only by the owner's share)
  that the requester does not hold yet; credentials are not requestable (only their
  owner shares them, 10.4).
* Dedupe: the same pending (resource, requester, level) is returned with 200 (9.7.1).
* Limits: at most ``MAX_PENDING`` open requests per requester per organization and
  ``MAX_PER_HOUR`` new ones per hour; above → 429 ``quota_exceeded`` (``rate``, ``user``).
* Expiry: pending rows older than 30 days turn ``expired`` when the organization's
  requests are next read or written (no sweep job; H-07 owns the schedulers).
"""

from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import denials
from app.authz.grant_validation import invalid
from app.authz.share_targets import ShareType
from app.models.access_request import AccessRequest

REQUEST_TTL: Final = timedelta(days=30)
MAX_PENDING: Final = 20
MAX_PER_HOUR: Final = 10
RANK: Final = {"consumer": 0, "viewer": 1, "editor": 2, "manager": 3}


def now() -> datetime:
    return datetime.now(UTC)


def level_permission(kind: ShareType, level: str) -> str:
    """The permission a level gives on the type (model.fga): what "already held" means."""
    if level == "consumer":
        return kind.min_visible
    if level == "viewer":
        return "can_view_config" if kind.name == "agent" else "can_view"
    if level == "editor":
        return "can_edit"
    raise invalid("level")


def check_requestable(kind: ShareType, level: str) -> None:
    if level not in kind.levels or RANK[level] > RANK["editor"]:
        raise invalid("level")


def check_approval_level(requested: str, chosen: str | None) -> str:
    """An approver may lower the level, never raise it (S18)."""
    level = chosen or requested
    if RANK[level] > RANK[requested]:
        raise invalid("level")
    return level


async def expire_stale(db: AsyncSession, org_id: UUID) -> None:
    await db.execute(
        update(AccessRequest)
        .where(
            AccessRequest.org_id == org_id,
            AccessRequest.status == "pending",
            AccessRequest.expires_at <= now(),
        )
        .values(status="expired", updated_at=now())
        .execution_options(synchronize_session=False)
    )


async def check_limits(db: AsyncSession, org_id: UUID, requester_id: UUID) -> None:
    mine = (AccessRequest.org_id == org_id, AccessRequest.requester_id == requester_id)
    pending = await db.scalar(select(func.count()).where(*mine, AccessRequest.status == "pending"))
    recent = await db.scalar(
        select(func.count()).where(*mine, AccessRequest.created_at >= now() - timedelta(hours=1))
    )
    if (pending or 0) >= MAX_PENDING:
        raise denials.quota_exceeded("rate", "user", limit=MAX_PENDING, used=pending)
    if (recent or 0) >= MAX_PER_HOUR:
        raise denials.quota_exceeded("rate", "user", limit=MAX_PER_HOUR, used=recent)


async def pending_duplicate(
    db: AsyncSession, org_id: UUID, kind: ShareType, resource_id: UUID, requester: UUID, level: str
) -> AccessRequest | None:
    return await db.scalar(
        select(AccessRequest).where(
            AccessRequest.org_id == org_id,
            AccessRequest.resource_type == kind.name,
            AccessRequest.resource_id == resource_id,
            AccessRequest.requester_id == requester,
            AccessRequest.relation == level,
            AccessRequest.status == "pending",
        )
    )
