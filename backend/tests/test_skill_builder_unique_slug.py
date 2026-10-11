from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.skill_builder import SkillBuilderStatus
from app.services import skill_builder_service
from app.services.skill_builder_mutation_scope import SkillBuilderMutationTarget
from app.skills import service as skill_service
from app.skills.creation import TextSkillCreation
from tests.authz.unit_scope_fixture import create_test_text_skill, unit_context
from tests.conftest import TEST_USER_ID


def _skill_content(*, name: str = "skill") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when summarizing Korean meeting notes."\n'
        "---\n\n"
        "Extract action items, owners, and due dates.\n"
    )


def _korean_draft() -> dict[str, JsonValue]:
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
        await create_test_text_skill(
            db,
            TextSkillCreation(
                name="Existing Skill",
                slug="skill",
                description="Existing slug owner.",
                content=_skill_content(name="existing-skill"),
            ),
            unit_context(TEST_USER_ID),
        )
        session = await skill_builder_service.create_session(
            db,
            context=unit_context(TEST_USER_ID),
            user_request="회의록 정리 스킬을 만들어줘",
        )
        await skill_builder_service.save_draft_package(
            db, SkillBuilderMutationTarget(session.id, unit_context(TEST_USER_ID)), _korean_draft()
        )

        created = await skill_builder_service.confirm_session(
            db, session, context=unit_context(TEST_USER_ID)
        )
        await db.commit()

    assert created.name == "회의록 액션 아이템 정리"
    assert created.slug == "skill-2"
    assert session.status == SkillBuilderStatus.COMPLETED.value
    assert session.finalized_skill_id == created.id
