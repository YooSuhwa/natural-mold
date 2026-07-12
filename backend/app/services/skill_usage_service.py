"""Skill-axis usage ledger (Phase 3 spec §3.1/§5).

Honest attribution only (D3):

* ``evaluation_run`` events carry the real LLM tokens/cost of a skill
  evaluation run — the run exists for exactly one skill, so attribution is
  exact.
* ``chat_execution`` events count ``execute_in_skill`` sandbox executions in
  chat. Scripts consume no LLM tokens, so only ``execution_count`` carries
  signal.

Recording must never fail the caller: the chat-path recorder swallows all
exceptions and uses its own session (the tool call runs inside a LangGraph
turn whose request session may be mid-teardown — same pattern as
``skill_executor_audit``).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.skill_usage_event import SkillUsageEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SkillUsageDailyPoint:
    date: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    execution_count: int


@dataclass(frozen=True, slots=True)
class SkillUsageSummary:
    days: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    priced_event_count: int
    unpriced_token_event_count: int
    evaluation_run_count: int
    chat_execution_count: int
    daily: list[SkillUsageDailyPoint]


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def record_evaluation_usage(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID,
    user_id: uuid.UUID,
    evaluation_run_id: uuid.UUID | None,
    model_name: str | None,
    tokens_in: int,
    tokens_out: int,
    cost_usd: Decimal | None,
) -> SkillUsageEvent:
    """Append one evaluation-run usage event. Caller owns the session/commit."""

    event = SkillUsageEvent(
        skill_id=skill_id,
        user_id=user_id,
        source_kind="evaluation_run",
        evaluation_run_id=evaluation_run_id,
        model_name=model_name,
        tokens_in=max(0, int(tokens_in)),
        tokens_out=max(0, int(tokens_out)),
        cost_usd=cost_usd,
        execution_count=0,
    )
    db.add(event)
    await db.flush()
    return event


async def record_evaluation_usage_nonfatal(
    *,
    skill_id: uuid.UUID,
    user_id: uuid.UUID,
    evaluation_run_id: uuid.UUID,
    model_name: str | None,
    usage: dict[str, object],
) -> None:
    """Own-session, best-effort evaluation-run ledger write.

    Called AFTER the run's completion is committed, so the completion is never
    rolled back if this write fails or is cancelled — ``asyncio.CancelledError``
    (a BaseException) would otherwise bypass a broad ``except Exception`` and
    unwind the run's transaction (repo CLAUDE.md cancel-vs-Exception rule).
    """

    raw_cost = usage.get("cost_usd")
    cost = (
        Decimal(str(raw_cost))
        if isinstance(raw_cost, int | float) and not isinstance(raw_cost, bool)
        else None
    )
    try:
        async with async_session() as db:
            await record_evaluation_usage(
                db,
                skill_id=skill_id,
                user_id=user_id,
                evaluation_run_id=evaluation_run_id,
                model_name=model_name,
                tokens_in=int(usage.get("tokens_in") or 0),
                tokens_out=int(usage.get("tokens_out") or 0),
                cost_usd=cost,
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — ledger write must not fail the run
        logger.warning(
            "skill evaluation usage ledger write failed run_id=%s",
            evaluation_run_id,
            exc_info=True,
        )


async def record_chat_execution(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
) -> SkillUsageEvent:
    """Append one chat ``execute_in_skill`` execution event."""

    event = SkillUsageEvent(
        skill_id=skill_id,
        user_id=user_id,
        source_kind="chat_execution",
        conversation_id=conversation_id,
        agent_id=agent_id,
        tokens_in=0,
        tokens_out=0,
        cost_usd=None,
        execution_count=1,
    )
    db.add(event)
    await db.flush()
    return event


async def record_chat_execution_nonfatal(
    *,
    skill_id: uuid.UUID,
    user_id: uuid.UUID,
    thread_id: str | None,
    agent_id: uuid.UUID | None,
) -> None:
    """Chat-path recorder — own session, swallows every failure.

    ``thread_id`` is the LangGraph thread id; for chat runs it equals the
    conversation UUID. Non-UUID thread ids (defensive) record with a NULL
    conversation.
    """

    conversation_id: uuid.UUID | None = None
    if thread_id:
        try:
            conversation_id = uuid.UUID(thread_id)
        except ValueError:
            conversation_id = None
    try:
        async with async_session() as db:
            await record_chat_execution(
                db,
                skill_id=skill_id,
                user_id=user_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — accounting must never fail the tool call
        logger.warning(
            "skill usage chat-execution write failed skill=%s thread=%s",
            skill_id,
            thread_id,
            exc_info=True,
        )


async def get_skill_usage_summary(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID,
    days: int = 30,
) -> SkillUsageSummary:
    """On-demand aggregate over the event ledger (no rollup table).

    Volume stays low (one event per evaluation run / per sandbox execution),
    so a windowed scan on ``(skill_id, created_at)`` is sufficient — spec §3.1.
    """

    window_days = max(1, min(days, 365))
    since = _utc_now_naive() - timedelta(days=window_days)

    totals_row = (
        await db.execute(
            select(
                func.coalesce(func.sum(SkillUsageEvent.tokens_in), 0),
                func.coalesce(func.sum(SkillUsageEvent.tokens_out), 0),
                func.coalesce(func.sum(SkillUsageEvent.cost_usd), 0),
                func.count(SkillUsageEvent.id).filter(SkillUsageEvent.cost_usd.is_not(None)),
                func.count(SkillUsageEvent.id).filter(
                    SkillUsageEvent.cost_usd.is_(None),
                    (SkillUsageEvent.tokens_in + SkillUsageEvent.tokens_out) > 0,
                ),
                func.count(SkillUsageEvent.id).filter(
                    SkillUsageEvent.source_kind == "evaluation_run"
                ),
                func.coalesce(
                    func.sum(SkillUsageEvent.execution_count).filter(
                        SkillUsageEvent.source_kind == "chat_execution"
                    ),
                    0,
                ),
            ).where(
                SkillUsageEvent.skill_id == skill_id,
                SkillUsageEvent.created_at >= since,
            )
        )
    ).one()

    date_bucket = func.date(SkillUsageEvent.created_at)
    daily_rows = (
        await db.execute(
            select(
                date_bucket.label("bucket"),
                func.coalesce(func.sum(SkillUsageEvent.tokens_in), 0),
                func.coalesce(func.sum(SkillUsageEvent.tokens_out), 0),
                func.coalesce(func.sum(SkillUsageEvent.cost_usd), 0),
                func.coalesce(
                    func.sum(SkillUsageEvent.execution_count).filter(
                        SkillUsageEvent.source_kind == "chat_execution"
                    ),
                    0,
                ),
            )
            .where(
                SkillUsageEvent.skill_id == skill_id,
                SkillUsageEvent.created_at >= since,
            )
            .group_by(date_bucket)
            .order_by(date_bucket.asc())
        )
    ).all()

    daily = [
        SkillUsageDailyPoint(
            date=str(bucket),
            tokens_in=int(tokens_in or 0),
            tokens_out=int(tokens_out or 0),
            cost_usd=float(cost or 0),
            execution_count=int(executions or 0),
        )
        for bucket, tokens_in, tokens_out, cost, executions in daily_rows
    ]

    (
        tokens_in,
        tokens_out,
        cost_usd,
        priced_count,
        unpriced_count,
        eval_count,
        execution_count,
    ) = totals_row
    return SkillUsageSummary(
        days=window_days,
        tokens_in=int(tokens_in or 0),
        tokens_out=int(tokens_out or 0),
        cost_usd=float(cost_usd or 0),
        priced_event_count=int(priced_count or 0),
        unpriced_token_event_count=int(unpriced_count or 0),
        evaluation_run_count=int(eval_count or 0),
        chat_execution_count=int(execution_count or 0),
        daily=daily,
    )
