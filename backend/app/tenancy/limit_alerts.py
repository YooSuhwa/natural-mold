"""80% / 100% ``usage.limit_warning`` notifications (plan 8.7, 14.2; H-06).

"80%에서 그 한도를 정한 관리자에게 알림(사용자 한도는 본인에게도)": company and
organization limits go to the company admins (company-level rows, ``org_id`` NULL); user
and agent limits go to the organization's admins, a user limit also to that user.

Each (scope, scope id, month, threshold) is sent once: the notification target id is
derived from those four values, so a recipient who already has the row is skipped
(across processes) and a 100% notice never merges into a read 80% one. A threshold
crossed together with a higher one is marked sent without its own notice. Written in its
own company session (the run-start transaction may still be rolled back by the 429),
bounded by ``ALERT_TIMEOUT``; failures are logged and retried on the next check.
"""

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Iterable
from decimal import Decimal
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.notifications import Target, notify
from app.authz.org_administrators import active_administrators
from app.authz.usage_limit_queries import company_admins
from app.database import SessionTenant, async_session, tenant_session
from app.models.notification import Notification
from app.models.organization import Organization
from app.tenancy.limit_usage import LimitState, Subject

logger = logging.getLogger(__name__)

KIND: Final = "usage.limit_warning"
TARGET_TYPE: Final = "usage_limit"
THRESHOLDS: Final = (80, 100)
ALERT_TIMEOUT: Final = 2.0
LINK: Final = "/settings/usage"
_SENT: OrderedDict[UUID, None] = OrderedDict()
_SENT_LIMIT: Final = 10_000


def alert_target(state: LimitState, threshold: int) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"moldy/usage-limit/{state.scope}/{state.scope_id}/{state.period.key}/{threshold}",
    )


def reset_sent() -> None:
    _SENT.clear()


def due_alerts(states: Iterable[LimitState]) -> list[tuple[LimitState, int, list[UUID]]]:
    """(state, highest crossed threshold, every crossed target id) not yet sent here."""
    due: list[tuple[LimitState, int, list[UUID]]] = []
    for state in states:
        if state.limit is None or state.limit <= 0:
            continue  # a zero limit blocks every run; nothing to warn about
        crossed = [t for t in THRESHOLDS if state.used * 100 >= state.limit * Decimal(t)]
        targets = [alert_target(state, t) for t in crossed]
        if crossed and any(target not in _SENT for target in targets):
            due.append((state, crossed[-1], targets))
    return due


async def recipients(db: AsyncSession, state: LimitState, subject: Subject) -> set[UUID]:
    if state.scope in ("tenant", "org"):
        return await company_admins(db, subject.tenant_id)
    organization = await db.get(Organization, subject.org_id)
    found = await active_administrators(db, organization) if organization else set()
    if state.scope == "user":
        found.add(subject.user_id)
    return found


async def _write(due: list[tuple[LimitState, int, list[UUID]]], subject: Subject) -> None:
    scope = SessionTenant(tenant_id=subject.tenant_id, user_id=subject.user_id)
    async with tenant_session(scope, maker=async_session) as db:
        for state, threshold, targets in due:
            target_id = targets[-1]
            sent = set(
                await db.scalars(
                    select(Notification.user_id).where(
                        Notification.kind == KIND,
                        Notification.tenant_id == subject.tenant_id,
                        Notification.target_id == target_id,
                    )
                )
            )
            fresh = await recipients(db, state, subject) - sent
            if not fresh:
                continue
            await notify(
                db,
                kind=KIND,
                recipients=sorted(fresh),
                tenant_id=subject.tenant_id,
                org_id=None if state.scope in ("tenant", "org") else subject.org_id,
                target=Target(TARGET_TYPE, target_id),
                payload={
                    "scope": state.scope,
                    "scope_id": str(state.scope_id),
                    "threshold": threshold,
                    "limit": str(state.limit),
                    "used": str(state.used),
                    "period": state.period.key,
                    "resets_at": state.period.resets_at.isoformat(),
                },
                link=LINK,
            )
        await db.commit()


async def send_alerts(states: Iterable[LimitState], subject: Subject) -> None:
    due = due_alerts(states)
    if not due:
        return
    try:
        await asyncio.wait_for(_write(due, subject), ALERT_TIMEOUT)
    except Exception:  # noqa: BLE001 - a warning never blocks or fails a run
        logger.warning("usage limit alert failed tenant=%s", subject.tenant_id, exc_info=True)
        return
    for _, _, targets in due:
        for target in targets:
            _SENT[target] = None
    while len(_SENT) > _SENT_LIMIT:
        _SENT.popitem(last=False)


__all__ = ["KIND", "TARGET_TYPE", "alert_target", "reset_sent", "send_alerts"]
