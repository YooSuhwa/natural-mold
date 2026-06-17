from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.skill import Skill
from app.models.system_llm_setting import SystemLlmSetting
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio

BASE = "/api/skill-builder"


async def _configure_system_llm(db: AsyncSession) -> None:
    credential = await credential_service.create(
        db,
        user_id=None,
        definition_key="openai",
        name="builder-key",
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


def _skill_content(name: str = "notes") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when summarizing notes into action items."\n'
        "---\n\n"
        "Use when summarizing meeting notes.\n"
    )


def _draft_payload() -> dict[str, object]:
    return {
        "name": "Notes",
        "slug": "notes",
        "description": "Use when summarizing notes into action items.",
        "files": [
            {"path": "SKILL.md", "content": _skill_content(), "role": "skill"},
            {
                "path": "agents/openai.yaml",
                "content": 'interface:\n  default_prompt: "$notes summarize"\n',
                "role": "metadata",
            },
        ],
        "credential_requirements": [],
        "execution_profile": {"requires_network": False},
    }


async def test_start_requires_system_llm(client: AsyncClient) -> None:
    response = await client.post(
        BASE,
        json={"mode": "create", "user_request": "회의록 스킬 만들어줘"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SYSTEM_LLM_NOT_CONFIGURED"


async def test_start_create_session(client: AsyncClient, db: AsyncSession) -> None:
    await _configure_system_llm(db)

    response = await client.post(
        BASE,
        json={"mode": "create", "user_request": "회의록 스킬 만들어줘"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mode"] == "create"
    assert body["status"] == "collecting"
    assert body["source_skill_id"] is None


async def test_start_improve_session_snapshots_owned_skill(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Notes",
            slug="notes",
            description="Use when summarizing notes.",
            content=_skill_content(),
        )
        await db.commit()

        response = await client.post(
            BASE,
            json={
                "mode": "improve",
                "source_skill_id": str(skill.id),
                "user_request": "더 정확하게 개선해줘",
            },
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mode"] == "improve"
    assert body["source_skill_id"] == str(skill.id)
    assert body["base_content_hash"] == skill.content_hash
    assert body["base_snapshot"]["files"][0]["path"] == "SKILL.md"


async def test_start_improve_unowned_skill_returns_404(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=uuid.uuid4(),
            name="Other",
            slug="other",
            description="Use when summarizing notes.",
            content=_skill_content("other"),
        )
        await db.commit()

        response = await client.post(
            BASE,
            json={
                "mode": "improve",
                "source_skill_id": str(skill.id),
                "user_request": "개선해줘",
            },
        )

    assert response.status_code == 404


async def test_validate_persists_result(client: AsyncClient, db: AsyncSession) -> None:
    await _configure_system_llm(db)
    start = await client.post(
        BASE,
        json={"mode": "create", "user_request": "회의록 스킬 만들어줘"},
    )
    session_id = start.json()["id"]

    response = await client.post(f"{BASE}/{session_id}/validate", json=_draft_payload())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draft_package"]["slug"] == "notes"
    assert body["validation_result"]["error_count"] == 0
    assert body["compatibility_result"]["targets"]


async def test_confirm_returns_skill_response(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        start = await client.post(
            BASE,
            json={"mode": "create", "user_request": "회의록 스킬 만들어줘"},
        )
        session_id = start.json()["id"]
        await client.post(f"{BASE}/{session_id}/validate", json=_draft_payload())

        response = await client.post(f"{BASE}/{session_id}/confirm")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "package"
    assert body["slug"] == "notes"


async def test_confirm_completed_session_is_idempotent(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        start = await client.post(
            BASE,
            json={"mode": "create", "user_request": "회의록 스킬 만들어줘"},
        )
        session_id = start.json()["id"]
        await client.post(f"{BASE}/{session_id}/validate", json=_draft_payload())

        first = await client.post(f"{BASE}/{session_id}/confirm")
        second = await client.post(f"{BASE}/{session_id}/confirm")

    result = await db.execute(
        select(Skill).where(Skill.user_id == TEST_USER_ID, Skill.slug == "notes")
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"]
    assert len(result.scalars().all()) == 1


async def test_confirm_improve_conflict_returns_409(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Notes",
            slug="notes",
            description="Use when summarizing notes.",
            content=_skill_content(),
        )
        await db.commit()
        start = await client.post(
            BASE,
            json={
                "mode": "improve",
                "source_skill_id": str(skill.id),
                "user_request": "개선해줘",
            },
        )
        await skill_service.update_text_content(
            db,
            skill=skill,
            content=_skill_content() + "\nManual change.\n",
        )
        await db.commit()
        session_id = start.json()["id"]
        await client.post(f"{BASE}/{session_id}/validate", json=_draft_payload())

        response = await client.post(f"{BASE}/{session_id}/confirm")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SKILL_BUILDER_SOURCE_CONFLICT"
