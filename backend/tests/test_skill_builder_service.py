from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.skill_builder import SkillBuilderMode, SkillBuilderStatus
from app.services import skill_builder_service
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID


def _skill_content(name: str = "notes") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when summarizing notes into action items."\n'
        "---\n\n"
        "Use when summarizing meeting notes.\n"
    )


@pytest.mark.asyncio
async def test_create_session_defaults_to_create_mode(db: AsyncSession) -> None:
    session = await skill_builder_service.create_session(
        db,
        user_id=TEST_USER_ID,
        user_request="회의록 스킬 만들어줘",
    )
    await db.commit()

    assert session.mode == SkillBuilderMode.CREATE.value
    assert session.status == SkillBuilderStatus.COLLECTING.value
    assert session.source_skill_id is None


@pytest.mark.asyncio
async def test_get_session_is_user_scoped(db: AsyncSession) -> None:
    session = await skill_builder_service.create_session(
        db,
        user_id=TEST_USER_ID,
        user_request="스킬 만들어줘",
    )
    await db.commit()

    other_user_id = uuid.uuid4()

    assert await skill_builder_service.get_session(db, session.id, TEST_USER_ID) == session
    assert await skill_builder_service.get_session(db, session.id, other_user_id) is None


@pytest.mark.asyncio
async def test_create_improve_session_snapshots_owned_skill(db: AsyncSession) -> None:
    skill = await skill_service.create_text_skill(
        db,
        user_id=TEST_USER_ID,
        name="Notes",
        slug="notes",
        description="Use when summarizing notes.",
        content=_skill_content(),
        version="1.2.3",
    )
    await db.commit()

    session = await skill_builder_service.create_session(
        db,
        user_id=TEST_USER_ID,
        user_request="이 스킬 개선해줘",
        mode=SkillBuilderMode.IMPROVE,
        source_skill_id=skill.id,
    )
    await db.commit()

    assert session.mode == SkillBuilderMode.IMPROVE.value
    assert session.source_skill_id == skill.id
    assert session.base_skill_version == "1.2.3"
    assert session.base_content_hash == skill.content_hash
    assert session.base_snapshot is not None
    assert session.base_snapshot["kind"] == "text"
    assert session.base_snapshot["files"][0]["path"] == "SKILL.md"


@pytest.mark.asyncio
async def test_create_improve_session_rejects_unowned_skill(db: AsyncSession) -> None:
    skill = await skill_service.create_text_skill(
        db,
        user_id=uuid.uuid4(),
        name="Other",
        slug="other",
        description="Use when summarizing notes.",
        content=_skill_content("other"),
    )
    await db.commit()

    with pytest.raises(skill_builder_service.SkillBuilderSourceSkillNotFound):
        await skill_builder_service.create_session(
            db,
            user_id=TEST_USER_ID,
            user_request="개선해줘",
            mode=SkillBuilderMode.IMPROVE,
            source_skill_id=skill.id,
        )


@pytest.mark.asyncio
async def test_append_message_and_save_draft(db: AsyncSession) -> None:
    session = await skill_builder_service.create_session(
        db,
        user_id=TEST_USER_ID,
        user_request="스킬 만들어줘",
    )

    await skill_builder_service.append_message(
        db,
        session,
        role="user",
        content="자료 정리 스킬",
    )
    await skill_builder_service.save_draft_package(
        db,
        session,
        draft={"name": "notes", "files": [{"path": "SKILL.md"}]},
    )
    await db.commit()

    assert session.messages is not None
    assert session.messages[0]["role"] == "user"
    assert session.draft_package == {"name": "notes", "files": [{"path": "SKILL.md"}]}
    assert session.status == SkillBuilderStatus.REVIEW.value


@pytest.mark.asyncio
async def test_confirm_session_create_mode_creates_package_skill(
    db: AsyncSession,
    tmp_path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        session = await skill_builder_service.create_session(
            db,
            user_id=TEST_USER_ID,
            user_request="스킬 만들어줘",
        )
        await skill_builder_service.save_draft_package(
            db,
            session,
            draft={
                "name": "Notes",
                "slug": "notes",
                "description": "Use when summarizing notes into action items.",
                "files": [
                    {"path": "SKILL.md", "content": _skill_content(), "role": "skill"},
                    {
                        "path": "agents/openai.yaml",
                        "content": "interface:\n  default_prompt: \"$notes summarize\"\n",
                        "role": "metadata",
                    },
                ],
                "credential_requirements": [
                    {
                        "key": "openai",
                        "definition_key": "openai",
                        "required": True,
                        "label": "OpenAI",
                        "fields": ["api_key"],
                        "env_map": {"api_key": "OPENAI_API_KEY"},
                    }
                ],
                "execution_profile": {"requires_network": False},
            },
        )

        skill = await skill_builder_service.confirm_session(db, session, user_id=TEST_USER_ID)
        await db.commit()

    assert skill.kind == "package"
    assert skill.credential_requirements is not None
    assert skill.execution_profile == {"requires_network": False}
    assert skill.current_revision_id is not None
    assert session.status == SkillBuilderStatus.COMPLETED.value
    assert session.finalized_skill_id == skill.id


@pytest.mark.asyncio
async def test_confirm_session_rejects_invalid_draft(db: AsyncSession) -> None:
    session = await skill_builder_service.create_session(
        db,
        user_id=TEST_USER_ID,
        user_request="스킬 만들어줘",
    )
    await skill_builder_service.save_draft_package(
        db,
        session,
        draft={
            "name": "Broken",
            "slug": "broken",
            "description": "Broken",
            "files": [{"path": "README.md", "content": "missing skill"}],
        },
    )

    with pytest.raises(skill_builder_service.SkillBuilderValidationError):
        await skill_builder_service.confirm_session(db, session, user_id=TEST_USER_ID)
