from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill_evaluation import SkillEvaluationRun
from app.services import skill_evaluation_service
from app.services.skill_evaluation_worker import SkillEvaluationWorker
from app.services.skill_evaluation_worker_state import mark_completed
from app.services.skill_evaluation_worker_types import SkillEvaluationResult
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID, TestSession

pytestmark = pytest.mark.asyncio


def _skill_content() -> str:
    return (
        "---\n"
        "name: evaluation-state\n"
        'description: "Use when testing evaluation state races."\n'
        "---\n\n"
        "Use when testing evaluation state races.\n"
    )


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", _skill_content())
    return buffer.getvalue()


async def test_mark_completed_preserves_concurrent_cancel(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_bytes(),
        )
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="Race",
        evals=[{"input": "race"}],
    )
    run = await skill_evaluation_service.create_run(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        evaluation_set=evaluation_set,
    )
    run.status = "grading"
    await db.commit()

    stale_run = await db.get(SkillEvaluationRun, run.id)
    assert stale_run is not None
    async with TestSession() as other:
        cancelling = await other.get(SkillEvaluationRun, run.id)
        assert cancelling is not None
        await skill_evaluation_service.cancel_run(other, cancelling, reason="user")
        await other.commit()

    completed = await mark_completed(
        db,
        stale_run,
        SkillEvaluationResult(
            summary={"case_count": 1, "pass_rate": 1},
            benchmark={"with_skill_pass_rate": 1, "without_skill_pass_rate": 0},
            case_results=[],
        ),
    )
    await db.commit()
    await db.refresh(stale_run)

    assert completed is False
    assert stale_run.status == "cancelled"
    assert stale_run.cancellation_requested_at is not None


async def test_worker_start_skips_when_leader_lock_is_not_acquired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def deny_leadership() -> bool:
        return False

    monkeypatch.setattr(
        "app.services.skill_evaluation_worker.try_acquire_skill_evaluation_worker_leader",
        deny_leadership,
    )
    worker = SkillEvaluationWorker()

    await worker.start()

    assert worker._task is None
