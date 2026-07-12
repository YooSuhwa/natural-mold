"""Phase 3 §7 — 휴먼 피드백 (스킬 단위 + 평가 케이스 단위, 표시 전용)."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.models.skill_feedback import SkillFeedback
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID, make_user

pytestmark = pytest.mark.asyncio


async def _create_skill(db: AsyncSession, tmp_path: Path, *, user_id=TEST_USER_ID):
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=user_id,
            name="Feedback",
            slug=f"feedback-{uuid.uuid4().hex[:8]}",
            description="Use when testing feedback.",
            content=(
                '---\nname: feedback\ndescription: "Use when testing feedback."\n---\n\nBody.\n'
            ),
            version="1.0.0",
        )
        await db.commit()
        return skill


async def _seed_completed_run(
    db: AsyncSession,
    skill_id: uuid.UUID,
    *,
    case_count: int = 2,
    status: str = "completed",
) -> SkillEvaluationRun:
    eval_set = SkillEvaluationSet(
        user_id=TEST_USER_ID,
        skill_id=skill_id,
        name="feedback",
        evals=[{"input": f"case-{i}"} for i in range(case_count)],
    )
    db.add(eval_set)
    await db.flush()
    run = SkillEvaluationRun(
        user_id=TEST_USER_ID,
        skill_id=skill_id,
        evaluation_set_id=eval_set.id,
        status=status,
        case_results=[
            {"case_index": i, "status": "passed", "score": 1.0} for i in range(case_count)
        ]
        if status == "completed"
        else None,
    )
    db.add(run)
    await db.commit()
    return run


# ---------------------------------------------------------------------------
# Skill-level feedback
# ---------------------------------------------------------------------------


async def test_skill_feedback_upsert_roundtrip(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _create_skill(db, tmp_path)

    empty = await client.get(f"/api/skills/{skill.id}/feedback")
    assert empty.status_code == 200
    assert empty.json() == {
        "skill_id": str(skill.id),
        "up_count": 0,
        "down_count": 0,
        "mine": None,
    }

    put_up = await client.put(
        f"/api/skills/{skill.id}/feedback",
        json={"rating": "up", "comment": "표 정리가 정확해요"},
    )
    assert put_up.status_code == 200, put_up.text
    body = put_up.json()
    assert body["up_count"] == 1
    assert body["down_count"] == 0
    assert body["mine"]["rating"] == "up"
    assert body["mine"]["comment"] == "표 정리가 정확해요"

    # Same user flips the rating — still one row (unique skill+user).
    put_down = await client.put(
        f"/api/skills/{skill.id}/feedback",
        json={"rating": "down"},
    )
    body = put_down.json()
    assert body["up_count"] == 0
    assert body["down_count"] == 1
    assert body["mine"]["comment"] is None

    delete = await client.delete(f"/api/skills/{skill.id}/feedback")
    assert delete.status_code == 204
    after = await client.get(f"/api/skills/{skill.id}/feedback")
    assert after.json()["mine"] is None
    assert after.json()["down_count"] == 0

    # Idempotent delete.
    again = await client.delete(f"/api/skills/{skill.id}/feedback")
    assert again.status_code == 204


async def test_skill_feedback_aggregates_other_users(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _create_skill(db, tmp_path)
    other = await make_user(db)
    db.add(SkillFeedback(skill_id=skill.id, user_id=other.id, rating="up"))
    await db.commit()

    response = await client.get(f"/api/skills/{skill.id}/feedback")
    body = response.json()
    assert body["up_count"] == 1
    assert body["mine"] is None  # someone else's rating, not mine


async def test_skill_feedback_unknown_and_foreign_skill_404(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    unknown = await client.get(f"/api/skills/{uuid.uuid4()}/feedback")
    assert unknown.status_code == 404

    other = await make_user(db)
    await db.commit()
    foreign = await _create_skill(db, tmp_path, user_id=other.id)
    response = await client.put(
        f"/api/skills/{foreign.id}/feedback",
        json={"rating": "up"},
    )
    assert response.status_code == 404  # enumeration-safe


# ---------------------------------------------------------------------------
# Per-case feedback
# ---------------------------------------------------------------------------


async def test_case_feedback_upsert_roundtrip(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _create_skill(db, tmp_path)
    run = await _seed_completed_run(db, skill.id)
    base = f"/api/skills/{skill.id}/evaluations/{run.evaluation_set_id}/runs/{run.id}"

    empty = await client.get(f"{base}/case-feedback")
    assert empty.status_code == 200
    assert empty.json() == []

    put = await client.put(
        f"{base}/case-feedback",
        json={"case_index": 1, "verdict": "disagree", "comment": "grader가 형식을 놓침"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["verdict"] == "disagree"
    assert put.json()["case_index"] == 1

    # Upsert — same case flips verdict without growing the list.
    await client.put(f"{base}/case-feedback", json={"case_index": 1, "verdict": "agree"})
    listing = await client.get(f"{base}/case-feedback")
    assert len(listing.json()) == 1
    assert listing.json()[0]["verdict"] == "agree"
    assert listing.json()[0]["comment"] is None

    delete = await client.delete(f"{base}/case-feedback/1")
    assert delete.status_code == 204
    assert (await client.get(f"{base}/case-feedback")).json() == []


async def test_case_feedback_rejects_out_of_range_and_incomplete_run(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _create_skill(db, tmp_path)
    run = await _seed_completed_run(db, skill.id, case_count=1)
    base = f"/api/skills/{skill.id}/evaluations/{run.evaluation_set_id}/runs/{run.id}"

    out_of_range = await client.put(
        f"{base}/case-feedback",
        json={"case_index": 5, "verdict": "agree"},
    )
    assert out_of_range.status_code == 422
    assert out_of_range.json()["error"]["code"] == "SKILL_FEEDBACK_INVALID"

    queued = await _seed_completed_run(db, skill.id, status="queued")
    queued_base = f"/api/skills/{skill.id}/evaluations/{queued.evaluation_set_id}/runs/{queued.id}"
    incomplete = await client.put(
        f"{queued_base}/case-feedback",
        json={"case_index": 0, "verdict": "agree"},
    )
    assert incomplete.status_code == 422


async def test_case_feedback_unknown_run_404(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _create_skill(db, tmp_path)
    run = await _seed_completed_run(db, skill.id)
    response = await client.put(
        f"/api/skills/{skill.id}/evaluations/{run.evaluation_set_id}/runs/{uuid.uuid4()}"
        "/case-feedback",
        json={"case_index": 0, "verdict": "agree"},
    )
    assert response.status_code == 404
