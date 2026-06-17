from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.skills import service as skill_service
from app.storage.paths import ensure_relative
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


async def test_manual_prepare_creates_eval_set_for_existing_skill(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: an existing package skill with embedded Moldy evals.
    skill = await _package_skill(db, tmp_path)

    # When: the manual prepare endpoint is called.
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        response = await client.post(
            f"/api/skills/{skill.id}/evaluations/prepare",
            json={"allow_llm_generation": False, "force": False},
        )

    # Then: a prepared evaluation set is returned and persisted.
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "created"
    assert body["source_kind"] == "manual_prepare"
    assert body["case_count"] == 1
    evaluation_set = await db.get(SkillEvaluationSet, uuid.UUID(body["evaluation_set_id"]))
    assert evaluation_set is not None
    assert evaluation_set.evals[0]["input"] == "Summarize."
    audit_event = await _latest_audit_event(db, "skill_evaluation_set.imported")
    assert audit_event is not None
    assert audit_event.target_id == str(skill.id)
    assert audit_event.event_metadata is not None
    assert audit_event.event_metadata["source_kind"] == "manual_prepare"
    assert audit_event.event_metadata["case_count"] == 1
    assert "Summarize." not in json.dumps(audit_event.event_metadata)


async def test_manual_prepare_does_not_enqueue_run(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: an existing package skill with importable evals.
    skill = await _package_skill(db, tmp_path)

    # When: the manual prepare endpoint creates a set.
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        response = await client.post(
            f"/api/skills/{skill.id}/evaluations/prepare",
            json={"allow_llm_generation": False},
        )

    # Then: no evaluation run is created.
    run_count = await db.scalar(select(func.count()).select_from(SkillEvaluationRun))
    assert response.status_code == 200, response.text
    assert run_count == 0


async def test_manual_prepare_respects_ownership(client: AsyncClient) -> None:
    # Given: a skill id that does not belong to the current user.
    missing_skill_id = "00000000-0000-0000-0000-000000000099"

    # When: manual prepare is requested.
    response = await client.post(
        f"/api/skills/{missing_skill_id}/evaluations/prepare",
        json={"allow_llm_generation": False},
    )

    # Then: the endpoint returns the same collapsed 404 as other skill APIs.
    assert response.status_code == 404


async def _package_skill(db: AsyncSession, tmp_path: Path) -> Skill:
    skill_id = uuid.uuid4()
    root = tmp_path / "skills" / str(skill_id)
    eval_dir = root / "evals"
    eval_dir.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: manual-prepare\n"
        'description: "Use when testing manual prepare."\n'
        "---\n\n"
        "Follow the task.\n",
        encoding="utf-8",
    )
    (eval_dir / "evals.json").write_text(
        json.dumps(
            {
                "name": "Manual smoke",
                "evals": [{"input": "Summarize.", "expected": "Summary."}],
            }
        ),
        encoding="utf-8",
    )
    skill = Skill(
        id=skill_id,
        user_id=TEST_USER_ID,
        name="Manual Prepare",
        slug=f"manual-prepare-{skill_id.hex[:8]}",
        description="Use when testing manual prepare.",
        kind="package",
        storage_path=ensure_relative(f"skills/{skill_id}"),
        content_hash="hash",
        size_bytes=1,
        version="1.0.0",
        package_metadata={"name": "manual-prepare"},
    )
    db.add(skill)
    await db.flush()
    await db.commit()
    return skill


async def _latest_audit_event(db: AsyncSession, action: str) -> AuditEvent | None:
    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.action == action)
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
