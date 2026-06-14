from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.audit_event import AuditEvent
from app.models.skill import Skill
from app.models.system_llm_setting import SystemLlmSetting
from app.routers import skill_evaluations as skill_evaluations_router
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


class RecordingEvaluationWorker:
    def __init__(self) -> None:
        self.reserved = False

    def reserve_slot(self) -> None:
        self.reserved = True

    def release_slot(self) -> None:
        self.reserved = False

    def enqueue(self, run_id: uuid.UUID, *, reserved: bool = False) -> None:
        if reserved:
            self.release_slot()


@pytest.fixture(autouse=True)
def evaluation_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        skill_evaluations_router,
        "skill_evaluation_worker",
        RecordingEvaluationWorker(),
    )


async def test_run_create_and_cancel_audits_are_sanitized(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    skill = await _create_skill(db, tmp_path)
    created = await client.post(
        f"/api/skills/{skill.id}/evaluations",
        json={"name": "Smoke", "evals": [{"input": "prompt-secret"}]},
    )
    set_id = created.json()["id"]

    run = await client.post(f"/api/skills/{skill.id}/evaluations/{set_id}/runs")
    await client.post(
        f"/api/skills/{skill.id}/evaluations/{set_id}/runs/{run.json()['id']}/cancel",
        json={"reason": "user"},
    )
    result = await db.execute(select(AuditEvent).order_by(AuditEvent.created_at))
    events = [
        event for event in result.scalars().all() if event.action.startswith("skill_evaluation.")
    ]

    assert [event.action for event in events] == [
        "skill_evaluation.run_create",
        "skill_evaluation.run_cancel",
    ]
    for event in events:
        assert event.event_metadata is not None
        assert event.event_metadata["skill_id"] == str(skill.id)
        assert event.event_metadata["evaluation_set_id"] == set_id
        assert "prompt-secret" not in str(event.event_metadata)


async def _configure_system_llm(db: AsyncSession) -> None:
    credential = await credential_service.create(
        db,
        user_id=None,
        definition_key="openai",
        name="evaluation-audit-key",
        data={"api_key": "sk-test"},
        is_system=True,
    )
    db.add(
        SystemLlmSetting(
            role="text_primary",
            credential_id=credential.id,
            model_name="gpt-5.4",
        )
    )
    await db.commit()


async def _create_skill(db: AsyncSession, tmp_path: Path) -> Skill:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Audit Probe",
            slug="audit-probe",
            description="Use when testing evaluation audit logs.",
            content=(
                "---\n"
                "name: audit-probe\n"
                'description: "Use when testing evaluation audit logs."\n'
                "---\n\n"
                "Test audit logs.\n"
            ),
            version="1.0.0",
        )
        await db.commit()
        return skill
