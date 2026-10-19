from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.schemas.skill_builder import SkillBuilderMode, SkillBuilderStatus
from app.services import skill_builder_service
from app.services.skill_builder_mutation_scope import (
    SkillBuilderMessage,
    SkillBuilderMutationTarget,
)
from app.skills.creation import TextSkillCreation
from tests.authz.unit_scope_fixture import create_test_text_skill, ensure_unit_scope, unit_context
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def explicit_scope_membership(db: AsyncSession) -> None:
    await ensure_unit_scope(db, CurrentUser(id=TEST_USER_ID, email="test@test.com", name="Test"))


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
        context=unit_context(TEST_USER_ID),
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
        context=unit_context(TEST_USER_ID),
        user_request="스킬 만들어줘",
    )
    await db.commit()

    other_user_id = uuid.uuid4()
    await ensure_unit_scope(
        db,
        CurrentUser(id=other_user_id, email="other-builder@test.local", name="Other Builder User"),
    )

    assert (
        await skill_builder_service.get_session(db, session.id, unit_context(TEST_USER_ID))
        == session
    )
    assert (
        await skill_builder_service.get_session(db, session.id, unit_context(other_user_id)) is None
    )


@pytest.mark.asyncio
async def test_create_improve_session_snapshots_owned_skill(db: AsyncSession) -> None:
    skill = await create_test_text_skill(
        db,
        TextSkillCreation(
            name="Notes",
            slug="notes",
            description="Use when summarizing notes.",
            content=_skill_content(),
            version="1.2.3",
        ),
        unit_context(TEST_USER_ID),
    )
    await db.commit()

    session = await skill_builder_service.create_session(
        db,
        context=unit_context(TEST_USER_ID),
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
    base_files = session.base_snapshot["files"]
    assert isinstance(base_files, list)
    base_file = base_files[0]
    assert isinstance(base_file, dict)
    assert base_file["path"] == "SKILL.md"


@pytest.mark.asyncio
async def test_create_improve_session_rejects_unowned_skill(db: AsyncSession) -> None:
    skill = await create_test_text_skill(
        db,
        TextSkillCreation(
            name="Other",
            slug="other",
            description="Use when summarizing notes.",
            content=_skill_content("other"),
        ),
        unit_context(uuid.uuid4()),
    )
    await db.commit()

    with pytest.raises(skill_builder_service.SkillBuilderSourceSkillNotFound):
        await skill_builder_service.create_session(
            db,
            context=unit_context(TEST_USER_ID),
            user_request="개선해줘",
            mode=SkillBuilderMode.IMPROVE,
            source_skill_id=skill.id,
        )


@pytest.mark.asyncio
async def test_append_message_and_save_draft(db: AsyncSession) -> None:
    session = await skill_builder_service.create_session(
        db,
        context=unit_context(TEST_USER_ID),
        user_request="스킬 만들어줘",
    )

    await skill_builder_service.append_message(
        db,
        SkillBuilderMutationTarget(session.id, unit_context(TEST_USER_ID)),
        SkillBuilderMessage("user", "자료 정리 스킬"),
    )
    await skill_builder_service.save_draft_package(
        db,
        SkillBuilderMutationTarget(session.id, unit_context(TEST_USER_ID)),
        {"name": "notes", "files": [{"path": "SKILL.md"}]},
    )
    await db.commit()

    assert session.messages is not None
    first_message = session.messages[0]
    assert isinstance(first_message, dict)
    assert first_message["role"] == "user"
    assert session.draft_package == {"name": "notes", "files": [{"path": "SKILL.md"}]}
    assert session.status == SkillBuilderStatus.REVIEW.value


@pytest.mark.asyncio
async def test_claim_for_confirming_transitions_review_session(db: AsyncSession) -> None:
    session = await skill_builder_service.create_session(
        db,
        context=unit_context(TEST_USER_ID),
        user_request="스킬 만들어줘",
    )
    await skill_builder_service.save_draft_package(
        db,
        SkillBuilderMutationTarget(session.id, unit_context(TEST_USER_ID)),
        {"name": "notes", "slug": "notes", "description": "d", "files": []},
    )

    claimed = await skill_builder_service.claim_for_confirming(
        db, session.id, unit_context(TEST_USER_ID)
    )
    reloaded = await skill_builder_service.get_session(db, session.id, unit_context(TEST_USER_ID))

    assert claimed is True
    assert reloaded is not None
    assert reloaded.status == SkillBuilderStatus.CONFIRMING.value


@pytest.mark.asyncio
async def test_claim_for_confirming_rejects_non_review_session(db: AsyncSession) -> None:
    session = await skill_builder_service.create_session(
        db,
        context=unit_context(TEST_USER_ID),
        user_request="스킬 만들어줘",
    )

    claimed = await skill_builder_service.claim_for_confirming(
        db, session.id, unit_context(TEST_USER_ID)
    )

    assert claimed is False
