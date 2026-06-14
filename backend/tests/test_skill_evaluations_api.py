from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


def _skill_content() -> str:
    return (
        "---\n"
        "name: evaluator\n"
        'description: "Use when testing skill evaluation behavior."\n'
        "---\n\n"
        "Use when testing evaluation behavior.\n"
    )


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
) -> None:
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


async def test_cancel_queued_run(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
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


async def test_list_evaluations_for_unowned_skill_returns_404(client: AsyncClient) -> None:
    response = await client.get(
        "/api/skills/00000000-0000-0000-0000-000000000099/evaluations"
    )

    assert response.status_code == 404
