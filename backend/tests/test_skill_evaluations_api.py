from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.skill_evaluation import SkillEvaluationRun
from app.models.system_llm_setting import SystemLlmSetting
from app.routers import skill_evaluations as skill_evaluations_router
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


class RecordingEvaluationWorker:
    def __init__(self) -> None:
        self.enqueued_run_ids: list[uuid.UUID] = []
        self.reserved_slots = 0

    def reserve_slot(self) -> None:
        self.reserved_slots += 1

    def release_slot(self) -> None:
        self.reserved_slots -= 1

    def enqueue(self, run_id: uuid.UUID, *, reserved: bool = False) -> None:
        if reserved:
            self.release_slot()
        self.enqueued_run_ids.append(run_id)


@pytest.fixture(autouse=True)
def evaluation_worker(monkeypatch) -> RecordingEvaluationWorker:
    worker = RecordingEvaluationWorker()
    monkeypatch.setattr(skill_evaluations_router, "skill_evaluation_worker", worker)
    return worker


def _skill_content() -> str:
    return (
        "---\n"
        "name: evaluator\n"
        'description: "Use when testing skill evaluation behavior."\n'
        "---\n\n"
        "Use when testing evaluation behavior.\n"
    )


async def _configure_system_llm(db: AsyncSession) -> None:
    credential = await credential_service.create(
        db,
        user_id=None,
        definition_key="openai",
        name="evaluation-key",
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


async def _create_skill(db: AsyncSession, tmp_path: Path):
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Evaluator",
            slug="evaluator",
            description="Use when testing skill evaluation behavior.",
            content=_skill_content(),
            version="1.0.0",
        )
        await db.commit()
        return skill


async def test_create_and_list_evaluation_sets(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _create_skill(db, tmp_path)

    create = await client.post(
        f"/api/skills/{skill.id}/evaluations",
        json={
            "name": "Smoke",
            "description": "Basic behavior",
            "evals": [{"input": "hello", "expected": "summary"}],
        },
    )
    listing = await client.get(f"/api/skills/{skill.id}/evaluations")

    assert create.status_code == 201, create.text
    assert listing.status_code == 200, listing.text
    assert listing.json()[0]["name"] == "Smoke"
    assert listing.json()[0]["latest_run"] is None


async def test_estimate_and_create_run(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
    evaluation_worker: RecordingEvaluationWorker,
) -> None:
    await _configure_system_llm(db)
    skill = await _create_skill(db, tmp_path)
    created = await client.post(
        f"/api/skills/{skill.id}/evaluations",
        json={"name": "Smoke", "evals": [{"input": "a"}, {"input": "b"}]},
    )
    set_id = created.json()["id"]

    estimate = await client.post(f"/api/skills/{skill.id}/evaluations/{set_id}/estimate")
    run = await client.post(f"/api/skills/{skill.id}/evaluations/{set_id}/runs")

    assert estimate.status_code == 200, estimate.text
    assert estimate.json()["case_count"] == 2
    assert run.status_code == 201, run.text
    assert run.json()["status"] == "queued"
    assert run.json()["skill_version"] == "1.0.0"
    assert run.json()["skill_content_hash"] == skill.content_hash
    assert evaluation_worker.enqueued_run_ids == [uuid.UUID(run.json()["id"])]
    assert evaluation_worker.reserved_slots == 0


async def test_create_run_returns_queue_full_when_worker_rejects(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
    monkeypatch,
) -> None:
    await _configure_system_llm(db)

    class FullEvaluationWorker:
        def reserve_slot(self) -> None:
            raise skill_evaluations_router.SkillEvaluationQueueFull("full")

        def release_slot(self) -> None:
            raise AssertionError("release_slot should not be called without a reservation")

        def enqueue(self, run_id: uuid.UUID, *, reserved: bool = False) -> None:
            raise AssertionError("enqueue should not be called when reservation fails")

    monkeypatch.setattr(
        skill_evaluations_router,
        "skill_evaluation_worker",
        FullEvaluationWorker(),
    )
    skill = await _create_skill(db, tmp_path)
    created = await client.post(
        f"/api/skills/{skill.id}/evaluations",
        json={"name": "Smoke", "evals": [{"input": "a"}]},
    )
    set_id = created.json()["id"]

    response = await client.post(f"/api/skills/{skill.id}/evaluations/{set_id}/runs")
    runs = await client.get(f"/api/skills/{skill.id}/evaluations/{set_id}/runs")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SKILL_EVALUATION_QUEUE_FULL"
    assert runs.status_code == 200
    assert runs.json() == []


async def test_cancel_queued_run(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    skill = await _create_skill(db, tmp_path)
    created = await client.post(
        f"/api/skills/{skill.id}/evaluations",
        json={"name": "Smoke", "evals": [{"input": "a"}]},
    )
    set_id = created.json()["id"]
    run = await client.post(f"/api/skills/{skill.id}/evaluations/{set_id}/runs")

    response = await client.post(
        f"/api/skills/{skill.id}/evaluations/{set_id}/runs/{run.json()['id']}/cancel",
        json={"reason": "user"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancellation_reason"] == "user"


async def test_cancel_running_run_sets_cancellation_timestamp(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    skill = await _create_skill(db, tmp_path)
    created = await client.post(
        f"/api/skills/{skill.id}/evaluations",
        json={"name": "Smoke", "evals": [{"input": "a"}]},
    )
    set_id = created.json()["id"]
    run = await client.post(f"/api/skills/{skill.id}/evaluations/{set_id}/runs")
    row = await db.get(SkillEvaluationRun, uuid.UUID(run.json()["id"]))
    assert row is not None
    row.status = "running"
    await db.commit()

    response = await client.post(
        f"/api/skills/{skill.id}/evaluations/{set_id}/runs/{run.json()['id']}/cancel",
        json={"reason": "user"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancellation_requested_at"] is not None


async def test_create_run_requires_required_credential_binding(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _create_skill(db, tmp_path)
    skill.credential_requirements = [
        {
            "key": "openai",
            "definition_key": "openai",
            "required": True,
            "label": "OpenAI",
            "fields": ["api_key"],
            "env_map": {"api_key": "OPENAI_API_KEY"},
        }
    ]
    await db.commit()
    created = await client.post(
        f"/api/skills/{skill.id}/evaluations",
        json={"name": "Smoke", "evals": [{"input": "a"}]},
    )
    set_id = created.json()["id"]

    response = await client.post(f"/api/skills/{skill.id}/evaluations/{set_id}/runs")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MARKETPLACE_CREDENTIAL_REQUIRED"


async def test_create_run_requires_system_llm_before_run_row(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
    evaluation_worker: RecordingEvaluationWorker,
) -> None:
    skill = await _create_skill(db, tmp_path)
    created = await client.post(
        f"/api/skills/{skill.id}/evaluations",
        json={"name": "Smoke", "evals": [{"input": "a"}]},
    )
    set_id = created.json()["id"]

    response = await client.post(f"/api/skills/{skill.id}/evaluations/{set_id}/runs")
    result = await db.execute(select(SkillEvaluationRun))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SYSTEM_LLM_NOT_CONFIGURED"
    assert result.scalars().all() == []
    assert evaluation_worker.enqueued_run_ids == []


async def test_list_evaluations_for_unowned_skill_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/skills/00000000-0000-0000-0000-000000000099/evaluations")

    assert response.status_code == 404
