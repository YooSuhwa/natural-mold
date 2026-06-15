from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill_evaluation import SkillEvaluationRun
from app.services import skill_evaluation_service
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID


def _skill_content() -> str:
    return (
        "---\n"
        "name: evaluator\n"
        'description: "Use when testing skill evaluation behavior."\n'
        "---\n\n"
        "Use when testing evaluation behavior.\n"
    )


async def _create_skill(db: AsyncSession):
    skill = await skill_service.create_text_skill(
        db,
        user_id=TEST_USER_ID,
        name="Evaluator",
        slug="evaluator",
        description="Use when testing skill evaluation behavior.",
        content=_skill_content(),
        version="1.0.0",
    )
    await db.flush()
    return skill


@pytest.mark.asyncio
async def test_create_evaluation_set_persists_cases(db: AsyncSession) -> None:
    skill = await _create_skill(db)

    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="Smoke",
        evals=[{"input": "hello", "expected": "summary"}],
    )
    await db.commit()

    assert evaluation_set.skill_id == skill.id
    assert evaluation_set.evals == [{"input": "hello", "expected": "summary"}]


@pytest.mark.asyncio
async def test_create_evaluation_set_rejects_empty_cases(db: AsyncSession) -> None:
    skill = await _create_skill(db)

    with pytest.raises(skill_evaluation_service.SkillEvaluationSetEmpty):
        await skill_evaluation_service.create_evaluation_set(
            db,
            user_id=TEST_USER_ID,
            skill=skill,
            name="Empty",
            evals=[],
        )


@pytest.mark.asyncio
async def test_estimate_run_uses_case_count_and_settings(db: AsyncSession) -> None:
    skill = await _create_skill(db)
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="Smoke",
        evals=[{"input": "a"}, {"input": "b"}],
    )

    estimate = skill_evaluation_service.estimate_run(evaluation_set)

    assert estimate.case_count == 2
    assert estimate.model_call_count == 6
    assert estimate.timeout_seconds == 180
    assert estimate.uses_baseline_comparison is True


@pytest.mark.asyncio
async def test_create_run_snapshots_skill_version_and_hash(db: AsyncSession) -> None:
    skill = await _create_skill(db)
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="Smoke",
        evals=[{"input": "a"}],
    )

    run = await skill_evaluation_service.create_run(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        evaluation_set=evaluation_set,
    )
    await db.commit()

    assert run.status == "queued"
    assert run.skill_version == "1.0.0"
    assert run.skill_content_hash == skill.content_hash
    assert run.estimate is not None
    assert run.estimate["case_count"] == 1


@pytest.mark.asyncio
async def test_cancel_run_transitions_queued_run(db: AsyncSession) -> None:
    skill = await _create_skill(db)
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="Smoke",
        evals=[{"input": "a"}],
    )
    run = await skill_evaluation_service.create_run(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        evaluation_set=evaluation_set,
    )

    await skill_evaluation_service.cancel_run(db, run, reason="user")
    await db.commit()

    assert run.status == "cancelled"
    assert run.cancellation_reason == "user"
    assert run.cancellation_requested_at is not None


@pytest.mark.asyncio
async def test_cancel_run_rejects_completed_run(db: AsyncSession) -> None:
    run = SkillEvaluationRun(
        user_id=TEST_USER_ID,
        skill_id=TEST_USER_ID,
        evaluation_set_id=TEST_USER_ID,
        status="completed",
    )

    with pytest.raises(skill_evaluation_service.SkillEvaluationRunNotCancellable):
        await skill_evaluation_service.cancel_run(db, run, reason="late")
