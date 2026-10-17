"""Per-company restart reconcile of interrupted skill evaluation runs (plan 8.9, H-07).

The leader lists the companies that own interrupted runs in one platform session, then
fails each company's runs in that company's own session, one company at a time (a failing
company is logged and skipped). A run's company is its skill's company — the same rule the
worker uses for queue items; system skills (no company) use a platform session.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionTenant, async_session, platform_session, tenant_session
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun
from app.services.skill_evaluation_worker_state import (
    RUN_LIFECYCLE_OPTIONS,
    mark_failed,
    record_run_audit,
)

logger = logging.getLogger(__name__)

INTERRUPTED = SkillEvaluationRun.status.in_(("running", "grading"))


def company_session(
    tenant_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    *,
    maker: Callable[[], AsyncSession] | None = None,
) -> AsyncSession:
    """The skill's company session; a system skill (``None``) runs in platform scope."""
    if tenant_id is None:
        return platform_session("skill_evaluation.system_skill", maker=maker or async_session)
    return tenant_session(SessionTenant(tenant_id, user_id), maker=maker or async_session)


def company_filter(tenant_id: uuid.UUID | None) -> ColumnElement[bool]:
    owner = Skill.tenant_id.is_(None) if tenant_id is None else Skill.tenant_id == tenant_id
    return SkillEvaluationRun.skill_id.in_(select(Skill.id).where(owner))


async def interrupted_run_companies(db: AsyncSession) -> list[uuid.UUID | None]:
    rows = await db.execute(
        select(Skill.tenant_id)
        .join(SkillEvaluationRun, SkillEvaluationRun.skill_id == Skill.id)
        .where(INTERRUPTED)
        .distinct()
    )
    return sorted(rows.scalars().all(), key=lambda tenant_id: str(tenant_id or ""))


async def mark_interrupted_runs(
    db: AsyncSession, *, company: ColumnElement[bool] | None = None
) -> int:
    query = select(SkillEvaluationRun).options(RUN_LIFECYCLE_OPTIONS).where(INTERRUPTED)
    if company is not None:
        query = query.where(company)
    result = await db.execute(query.execution_options(autoflush=False))
    rows = list(result.scalars().all())
    for run in rows:
        await mark_failed(db, run, "interrupted: process restarted")
        await record_run_audit(
            db,
            run,
            "skill_evaluation.run_fail",
            outcome="failure",
            metadata={"reason": "worker_restart"},
        )
    await db.flush()
    return len(rows)


async def reconcile_interrupted_by_company(
    session_factory: Callable[[], AsyncSession] | None = None,
) -> int:
    async with platform_session(
        "skill_evaluation.reconcile", maker=session_factory or async_session
    ) as db:
        companies = await interrupted_run_companies(db)
    count = 0
    for tenant_id in companies:
        try:
            async with company_session(tenant_id, None, maker=session_factory) as db:
                count += await mark_interrupted_runs(db, company=company_filter(tenant_id))
                await db.commit()
        except Exception:  # noqa: BLE001 — one company's failure must not block the others
            logger.warning(
                "interrupted run reconcile failed tenant_id=%s", tenant_id, exc_info=True
            )
    return count
