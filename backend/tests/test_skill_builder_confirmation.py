from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.skill_builder import SkillBuilderMode, SkillBuilderStatus
from app.services import skill_builder_service, skill_revision_service
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID


def _zip_with(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(name, data)
    return buffer.getvalue()


def _skill_content(name: str = "notes") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when summarizing notes into action items."\n'
        "---\n\n"
        "Use when summarizing meeting notes.\n"
    )


@pytest.mark.asyncio
async def test_confirm_session_create_mode_creates_package_skill(
    db: AsyncSession,
    tmp_path: Path,
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
                        "content": 'interface:\n  default_prompt: "$notes summarize"\n',
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
    assert skill.origin_kind == "created_by_me"
    assert skill.source_kind == "user"
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


@pytest.mark.asyncio
async def test_confirm_session_improve_mode_updates_existing_skill(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Notes",
            slug="notes",
            description="Use when summarizing notes.",
            content=_skill_content(),
        )
        session = await skill_builder_service.create_session(
            db,
            user_id=TEST_USER_ID,
            user_request="불릿 중심으로 개선해줘",
            mode=SkillBuilderMode.IMPROVE,
            source_skill_id=skill.id,
        )
        session.changelog_draft = {"summary": "Added bullet-point output guidance."}
        await skill_builder_service.save_draft_package(
            db,
            session,
            draft={
                "name": "Notes",
                "slug": "notes",
                "description": "Use when summarizing notes into action items.",
                "files": [
                    {
                        "path": "SKILL.md",
                        "content": _skill_content()
                        + "\nReturn concise bullet points with owners.\n",
                        "role": "skill",
                    },
                    {
                        "path": "agents/openai.yaml",
                        "content": 'interface:\n  default_prompt: "$notes summarize"\n',
                        "role": "metadata",
                    },
                ],
                "credential_requirements": [],
                "execution_profile": {"requires_network": False},
            },
        )

        updated = await skill_builder_service.confirm_session(db, session, user_id=TEST_USER_ID)
        revisions = await skill_revision_service.list_revisions(
            db,
            skill=updated,
            user_id=TEST_USER_ID,
        )
        await db.commit()

        assert updated.id == skill.id
        assert updated.kind == "package"
        assert updated.storage_path == f"skills/{skill.id}"
        assert updated.execution_profile == {"requires_network": False}
        assert b"Return concise bullet points" in skill_service.get_file_bytes(
            updated,
            "SKILL.md",
        )
        assert session.status == SkillBuilderStatus.COMPLETED.value
        assert session.finalized_skill_id == skill.id
        assert revisions[0].operation == "builder_improvement"
        assert revisions[0].changelog_summary == "Added bullet-point output guidance."


@pytest.mark.asyncio
async def test_confirm_session_improve_package_preserves_unchanged_files(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_with(
                {
                    "SKILL.md": _skill_content(),
                    "scripts/helper.py": "print('keep')\n",
                }
            ),
        )
        session = await skill_builder_service.create_session(
            db,
            user_id=TEST_USER_ID,
            user_request="출력 지침만 개선해줘",
            mode=SkillBuilderMode.IMPROVE,
            source_skill_id=skill.id,
        )
        await skill_builder_service.save_draft_package(
            db,
            session,
            draft={
                "name": "Notes",
                "slug": "notes",
                "description": "Use when summarizing notes into action items.",
                "files": [
                    {
                        "path": "SKILL.md",
                        "content": _skill_content() + "\nAlways include owners.\n",
                        "role": "skill",
                    },
                    {
                        "path": "scripts/helper.py",
                        "content": "print('keep')\n",
                        "role": "script",
                    },
                ],
                "credential_requirements": [],
                "execution_profile": {},
            },
        )

        updated = await skill_builder_service.confirm_session(db, session, user_id=TEST_USER_ID)
        await db.commit()

        assert b"Always include owners" in skill_service.get_file_bytes(updated, "SKILL.md")
        assert skill_service.get_file_bytes(updated, "scripts/helper.py") == b"print('keep')\n"


@pytest.mark.asyncio
async def test_confirm_session_improve_mode_rejects_stale_base_hash(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Notes",
            slug="notes",
            description="Use when summarizing notes.",
            content=_skill_content(),
        )
        session = await skill_builder_service.create_session(
            db,
            user_id=TEST_USER_ID,
            user_request="개선해줘",
            mode=SkillBuilderMode.IMPROVE,
            source_skill_id=skill.id,
        )
        await skill_service.update_text_content(
            db,
            skill=skill,
            content=_skill_content() + "\nManual edit before builder apply.\n",
        )
        await skill_builder_service.save_draft_package(
            db,
            session,
            draft={
                "name": "Notes",
                "slug": "notes",
                "description": "Use when summarizing notes into action items.",
                "files": [{"path": "SKILL.md", "content": _skill_content(), "role": "skill"}],
                "credential_requirements": [],
                "execution_profile": {},
            },
        )

        with pytest.raises(skill_builder_service.SkillBuilderConflictError):
            await skill_builder_service.confirm_session(db, session, user_id=TEST_USER_ID)

        await db.commit()

        assert skill.kind == "text"
        assert "Manual edit before builder apply" in await skill_service.read_text_content(skill)
        assert session.status == SkillBuilderStatus.REVIEW.value
