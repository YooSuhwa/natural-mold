from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


async def test_skill_detail_marks_latest_evaluation_stale_by_hash(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Stale Probe",
            slug="stale-probe",
            description="Use when testing stale evaluation state.",
            content=(
                "---\n"
                "name: stale-probe\n"
                'description: "Use when testing stale evaluation state."\n'
                "---\n\n"
                "Test stale evaluation state.\n"
            ),
            version="1.0.0",
        )
    original_hash = skill.content_hash
    evaluation_set_id = uuid.uuid4()
    db.add(
        SkillEvaluationSet(
            id=evaluation_set_id,
            user_id=TEST_USER_ID,
            skill_id=skill.id,
            name="Smoke",
            evals=[{"input": "a"}],
        )
    )
    db.add(
        SkillEvaluationRun(
            user_id=TEST_USER_ID,
            skill_id=skill.id,
            evaluation_set_id=evaluation_set_id,
            status="completed",
            skill_content_hash=original_hash,
            summary={"pass_rate": 1},
        )
    )
    skill.content_hash = "b" * 64
    await db.commit()

    response = await client.get(f"/api/skills/{skill.id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["latest_evaluation_summary"]["status"] == "stale"
    assert body["latest_evaluation_summary"]["skill_content_hash"] == original_hash
    assert body["health"]["state"] == "needs_rerun"
