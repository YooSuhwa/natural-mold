"""Usage limits: four spend tiers and the company run limits (plan 8.7, 8.4, D25; H-06).

``UsageLimits.check(db, subject)`` decides one model-call subject against every
configured monthly spend limit, company first: the first one at or above 100% raises
``429 quota_exceeded`` with ``kind=monthly_spend``, its ``scope`` (tenant, org, user,
agent) and ``resets_at``; ``limit`` and ``used`` only when the caller may see that
scope's amounts (``usage_limit_queries.can_view_amounts``). 80% and 100% send
``usage.limit_warning`` (``limit_alerts``).

Cache: states are kept per subject for ``CACHE_SECONDS`` (10 s; plan "요청 단위 캐시
10초") only while every limit is below 80%; at or above 80% each check re-reads the
aggregates. Saving a limit clears this process's cache.

Overshoot bound: spend is booked when a run's usage is reported (spend hook) and written
by the spend writer within its flush interval (5 s), so a subject can pass a limit by
the usage of the runs in flight plus up to 5 s of booked spend — and, below 80%, by what
is booked within the 10 s cache window.

``admit_run`` is the run-start gate called where a run row is created
(``create_run_from_locked_proof``): company concurrent runs and request rate
(``limit_capacity``), then the spend check. A resume continues an accepted run and is not
refused at start. During runs ``UsageLimitGuardMiddleware`` (installed by runtime
preparation for every root and child runtime whose subject has any limit) checks again
before each model call and ends the run at 100%. With no limit configured (the legacy
default) nothing is refused and no guard is installed.
"""

import time
from collections import OrderedDict
from decimal import Decimal
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import denials
from app.authz.usage_limit_queries import can_view_amounts
from app.exceptions import AppError
from app.models.organization import Tenant
from app.tenancy.attribution import RunAttribution
from app.tenancy.limit_alerts import send_alerts
from app.tenancy.limit_capacity import require_run_capacity
from app.tenancy.limit_usage import LimitState, Subject, limit_states

CACHE_SECONDS: Final = 10.0
NEAR_PERCENT: Final = Decimal(80)
_CACHE: OrderedDict[Subject, tuple[float, tuple[LimitState, ...]]] = OrderedDict()
_CACHE_LIMIT: Final = 4096


def reset_cache() -> None:
    _CACHE.clear()


def _near(states: tuple[LimitState, ...]) -> bool:
    return any(
        state.limit is not None and state.used * 100 >= state.limit * NEAR_PERCENT
        for state in states
    )


def quota_error(state: LimitState, *, amounts: bool) -> AppError:
    return denials.quota_exceeded(
        "monthly_spend",
        state.scope,
        limit=float(state.limit or 0) if amounts else None,
        used=float(state.used) if amounts else None,
        resets_at=state.period.resets_at,
    )


class UsageLimits:
    """The plan's ``UsageLimits.check`` entry point (stateless; the cache is module-wide)."""

    @staticmethod
    async def states(db: AsyncSession, subject: Subject) -> tuple[LimitState, ...]:
        cached = _CACHE.get(subject)
        now = time.monotonic()
        if cached is not None and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        states = await limit_states(db, subject)
        if _near(states):
            _CACHE.pop(subject, None)  # ≥ 80%: every check reads fresh aggregates
            return states
        _CACHE[subject] = (now, states)
        _CACHE.move_to_end(subject)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
        return states

    @classmethod
    async def blocking(cls, db: AsyncSession, subject: Subject) -> LimitState | None:
        """The first exhausted limit (company → agent) after sending due warnings."""
        states = await cls.states(db, subject)
        await send_alerts(states, subject)
        return next((state for state in states if state.exceeded), None)

    @classmethod
    async def check(cls, db: AsyncSession, subject: Subject) -> None:
        blocked = await cls.blocking(db, subject)
        if blocked is None:
            return
        amounts = await can_view_amounts(
            db,
            blocked.scope,
            tenant_id=subject.tenant_id,
            org_id=subject.org_id,
            user_id=subject.user_id,
        )
        raise quota_error(blocked, amounts=amounts)


def subject_of(attribution: RunAttribution) -> Subject:
    return Subject(
        tenant_id=attribution.tenant_id,
        org_id=attribution.org_id,
        user_id=attribution.caller_user_id,
        agent_id=attribution.actor_agent_id,
    )


async def admit_run(db: AsyncSession, attribution: RunAttribution | None, *, enforce: bool) -> None:
    if attribution is None or not enforce:
        return
    subject = subject_of(attribution)
    limits = await db.scalar(select(Tenant.limits).where(Tenant.id == subject.tenant_id))
    await require_run_capacity(db, subject.tenant_id, limits or {})
    await UsageLimits.check(db, subject)


__all__ = ["CACHE_SECONDS", "Subject", "UsageLimits", "admit_run", "quota_error", "reset_cache"]
