from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.skill_builder import SkillBuilderStatus
from app.services import skill_builder_service
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID


def _skill_content(*, name: str = "skill") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when summarizing Korean meeting notes."\n'
        "---\n\n"
        "Extract action items, owners, and due dates.\n"
    )


def _korean_draft() -> dict[str, object]:
    name = "회의록 액션 아이템 정리"
    return {
        "name": name,
        "slug": name,
        "description": "Use when extracting Korean meeting action items.",
        "files": [
            {"path": "SKILL.md", "content": _skill_content(), "role": "skill"},
            {
                "path": "agents/openai.yaml",
                "content": 'interface:\n  default_prompt: "$skill summarize"\n',
                "role": "metadata",
            },
        ],
        "credential_requirements": [],
        "execution_profile": {"requires_network": False},
    }


@pytest.mark.asyncio
async def test_confirm_create_uses_unique_slug_when_korean_draft_falls_back_to_skill(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Existing Skill",
            slug="skill",
            description="Existing slug owner.",
            content=_skill_content(name="existing-skill"),
        )
        session = await skill_builder_service.create_session(
            db,
            user_id=TEST_USER_ID,
            user_request="회의록 정리 스킬을 만들어줘",
        )
        await skill_builder_service.save_draft_package(db, session, draft=_korean_draft())

        created = await skill_builder_service.confirm_session(db, session, user_id=TEST_USER_ID)
        await db.commit()

    assert created.name == "회의록 액션 아이템 정리"
    assert created.slug == "skill-2"
    assert session.status == SkillBuilderStatus.COMPLETED.value
    assert session.finalized_skill_id == created.id
