"""Company member, concurrent-run and request-rate limits (plan 8.7 "그 밖의 회사 한도"; H-06).

Stored in ``tenants.limits`` next to the spend limit (absent/NULL = no limit):

* ``member_limit`` — company members that are not removed (active + suspended), checked
  when a member is added, a company invitation is created, and an invitation is accepted;
* ``concurrent_run_limit`` — the company's active (queued, running, canceling) runs,
  checked at run start;
* ``request_rate_limit`` — run starts per minute for the company. Like the existing
  ``slowapi`` limits it is per process and only counted while ``rate_limit_enabled``
  is on; the key is the company id.

Each breach is ``429 quota_exceeded`` with ``kind`` (``members``, ``concurrent_runs``,
``rate``), ``scope=tenant``, ``limit`` and ``used``.
"""

import time
from collections import deque
from collections.abc import Mapping
from typing import Final
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import denials
from app.authz.usage_limit_queries import is_company_member_email
from app.config import settings
from app.models.conversation_run import RUN_ACTIVE_STATUSES, ConversationRun
from app.models.organization import Tenant, TenantMember
from app.tenancy.limit_usage import int_limit

RATE_WINDOW_SECONDS: Final = 60.0
_STARTS: dict[UUID, deque[float]] = {}


def reset_rate_windows() -> None:
    _STARTS.clear()


async def member_count(db: AsyncSession, tenant_id: UUID) -> int:
    query = select(func.count()).where(
        TenantMember.tenant_id == tenant_id, TenantMember.status != "removed"
    )
    return int(await db.scalar(query) or 0)


async def active_run_count(db: AsyncSession, tenant_id: UUID) -> int:
    query = select(func.count()).where(
        ConversationRun.tenant_id == tenant_id,
        ConversationRun.status.in_(RUN_ACTIVE_STATUSES),
    )
    return int(await db.scalar(query) or 0)


async def require_member_capacity(db: AsyncSession, tenant_id: UUID) -> None:
    """One more company member must fit ``member_limit``.

    Counted under the company row lock the caller already holds — every caller takes it
    first in the company-first order (``tenants.members.add_member`` via
    ``lock_tenant_member_write``, company/organization invitation acceptance via
    ``locked_invitation_by_digest`` / ``locked_invitation_scope``, registration via
    ``locked_registration_scope``), so concurrent adds of one company serialize. No
    second ``FOR UPDATE`` here: re-locking the parent after a child row breaks the
    lock-order ratchet (``test_registration_membership_write_lock_order``).
    """
    limits = await db.scalar(select(Tenant.limits).where(Tenant.id == tenant_id))
    cap = int_limit((limits or {}).get("member_limit"))
    if cap is None:
        return
    used = await member_count(db, tenant_id)
    if used + 1 > cap:
        raise denials.quota_exceeded("members", "tenant", limit=cap, used=used)


async def require_invite_capacity(db: AsyncSession, tenant_id: UUID, email: str) -> None:
    """An invitation for someone not yet a member must leave room for them."""
    if not await is_company_member_email(db, tenant_id, email):
        await require_member_capacity(db, tenant_id)


def _take_rate_slot(tenant_id: UUID, cap: int) -> None:
    now = time.monotonic()
    window = _STARTS.setdefault(tenant_id, deque())
    while window and now - window[0] >= RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= cap:
        raise denials.quota_exceeded("rate", "tenant", limit=cap, used=len(window))
    window.append(now)


async def require_run_capacity(
    db: AsyncSession, tenant_id: UUID, limits: Mapping[str, object]
) -> None:
    """A new run must fit the company's concurrent runs, then its request rate."""
    cap = int_limit(limits.get("concurrent_run_limit"))
    if cap is not None:
        used = await active_run_count(db, tenant_id)
        if used >= cap:
            raise denials.quota_exceeded("concurrent_runs", "tenant", limit=cap, used=used)
    rate = int_limit(limits.get("request_rate_limit"))
    if rate is not None and settings.rate_limit_enabled:
        _take_rate_slot(tenant_id, rate)


__all__ = [
    "active_run_count",
    "member_count",
    "require_invite_capacity",
    "require_member_capacity",
    "require_run_capacity",
    "reset_rate_windows",
]
