"""휴먼 피드백 서비스 (Phase 3 §7, D2) — 표시 전용.

두 축을 다룬다:

* 스킬 단위 up/down (+코멘트) — ``skill_feedbacks``, (skill, user) 유니크.
* 평가 런 케이스별 agree/disagree (+코멘트) — grader 판정 검증,
  ``skill_evaluation_case_feedbacks``, (run, user, case_index) 유니크.

어느 쪽도 pass_rate/health 계산에 반영되지 않는다 — 반영은 후속.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill_evaluation import (
    SKILL_EVALUATION_CASE_FEEDBACK_VERDICTS,
    SkillEvaluationCaseFeedback,
    SkillEvaluationRun,
)
from app.models.skill_feedback import SKILL_FEEDBACK_RATINGS, SkillFeedback


class SkillFeedbackInvalid(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SkillFeedbackSummary:
    up_count: int
    down_count: int
    mine: SkillFeedback | None


# ---------------------------------------------------------------------------
# Skill-level feedback
# ---------------------------------------------------------------------------


async def get_skill_feedback_summary(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID,
    user_id: uuid.UUID,
) -> SkillFeedbackSummary:
    counts = (
        await db.execute(
            select(
                func.count(SkillFeedback.id).filter(SkillFeedback.rating == "up"),
                func.count(SkillFeedback.id).filter(SkillFeedback.rating == "down"),
            ).where(SkillFeedback.skill_id == skill_id)
        )
    ).one()
    mine = (
        await db.execute(
            select(SkillFeedback).where(
                SkillFeedback.skill_id == skill_id,
                SkillFeedback.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    return SkillFeedbackSummary(
        up_count=int(counts[0] or 0),
        down_count=int(counts[1] or 0),
        mine=mine,
    )


async def upsert_skill_feedback(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID,
    user_id: uuid.UUID,
    rating: str,
    comment: str | None,
) -> SkillFeedback:
    if rating not in SKILL_FEEDBACK_RATINGS:
        raise SkillFeedbackInvalid(f"rating must be one of {SKILL_FEEDBACK_RATINGS}")
    row = (
        await db.execute(
            select(SkillFeedback).where(
                SkillFeedback.skill_id == skill_id,
                SkillFeedback.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = SkillFeedback(
            skill_id=skill_id,
            user_id=user_id,
            rating=rating,
            comment=comment,
        )
        db.add(row)
    else:
        row.rating = rating
        row.comment = comment
    await db.flush()
    return row


async def delete_skill_feedback(
    db: AsyncSession,
    *,
    skill_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    row = (
        await db.execute(
            select(SkillFeedback).where(
                SkillFeedback.skill_id == skill_id,
                SkillFeedback.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    await db.flush()
    return True


# ---------------------------------------------------------------------------
# Per-case feedback on an evaluation run
# ---------------------------------------------------------------------------


async def list_case_feedback(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[SkillEvaluationCaseFeedback]:
    rows = (
        await db.execute(
            select(SkillEvaluationCaseFeedback)
            .where(
                SkillEvaluationCaseFeedback.run_id == run_id,
                SkillEvaluationCaseFeedback.user_id == user_id,
            )
            .order_by(SkillEvaluationCaseFeedback.case_index.asc())
        )
    ).scalars()
    return list(rows)


async def upsert_case_feedback(
    db: AsyncSession,
    *,
    run: SkillEvaluationRun,
    user_id: uuid.UUID,
    case_index: int,
    verdict: str,
    comment: str | None,
) -> SkillEvaluationCaseFeedback:
    if verdict not in SKILL_EVALUATION_CASE_FEEDBACK_VERDICTS:
        raise SkillFeedbackInvalid(
            f"verdict must be one of {SKILL_EVALUATION_CASE_FEEDBACK_VERDICTS}"
        )
    if run.status != "completed":
        raise SkillFeedbackInvalid("case feedback requires a completed run")
    case_count = len(run.case_results or [])
    if case_index < 0 or case_index >= case_count:
        raise SkillFeedbackInvalid(f"case_index out of range (0..{case_count - 1})")
    row = (
        await db.execute(
            select(SkillEvaluationCaseFeedback).where(
                SkillEvaluationCaseFeedback.run_id == run.id,
                SkillEvaluationCaseFeedback.user_id == user_id,
                SkillEvaluationCaseFeedback.case_index == case_index,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = SkillEvaluationCaseFeedback(
            run_id=run.id,
            user_id=user_id,
            case_index=case_index,
            verdict=verdict,
            comment=comment,
        )
        db.add(row)
    else:
        row.verdict = verdict
        row.comment = comment
    await db.flush()
    return row


async def delete_case_feedback(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    case_index: int,
) -> bool:
    row = (
        await db.execute(
            select(SkillEvaluationCaseFeedback).where(
                SkillEvaluationCaseFeedback.run_id == run_id,
                SkillEvaluationCaseFeedback.user_id == user_id,
                SkillEvaluationCaseFeedback.case_index == case_index,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    await db.flush()
    return True
