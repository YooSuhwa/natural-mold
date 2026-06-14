from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.skill_builder import SkillBuilderMode
from app.services import skill_builder_service, skill_revision_service
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID


def _skill_md(name: str = "notes", body: str = "Use when summarizing notes.") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when summarizing notes into action items."\n'
        "---\n\n"
        f"{body}\n"
    )


def _zip_with(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_confirm_create_generates_revision_changelog(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        session = await skill_builder_service.create_session(
            db,
            user_id=TEST_USER_ID,
            user_request="Create a notes skill",
        )
        await skill_builder_service.save_draft_package(
            db,
            session,
            draft={
                "name": "Notes",
                "slug": "notes",
                "description": "Use when summarizing notes.",
                "files": [
                    {"path": "SKILL.md", "content": _skill_md(), "role": "skill"},
                    {
                        "path": "agents/openai.yaml",
                        "content": "interface:\n  default_prompt: notes\n",
                        "role": "metadata",
                    },
                ],
                "credential_requirements": [],
                "execution_profile": {},
            },
        )

        skill = await skill_builder_service.confirm_session(db, session, user_id=TEST_USER_ID)
        revisions = await skill_revision_service.list_revisions(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
        )
        skill_body = skill_service.get_file_bytes(skill, "SKILL.md").decode()

    assert revisions[0].changelog_summary == "Created skill package with 2 files."
    assert revisions[0].changelog_items == [
        {"operation": "added", "path": "SKILL.md"},
        {"operation": "added", "path": "agents/openai.yaml"},
    ]
    assert "Created skill package" not in skill_body


@pytest.mark.asyncio
async def test_confirm_improve_generates_diff_changelog_items(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_with(
                {
                    "SKILL.md": _skill_md("notes", "Original guidance."),
                    "scripts/run.py": "print('v1')\n",
                    "references/old.md": "remove me\n",
                }
            ),
        )
        session = await skill_builder_service.create_session(
            db,
            user_id=TEST_USER_ID,
            user_request="Improve the notes skill",
            mode=SkillBuilderMode.IMPROVE,
            source_skill_id=skill.id,
        )
        await skill_builder_service.save_draft_package(
            db,
            session,
            draft={
                "name": "Notes",
                "slug": "notes",
                "description": "Use when summarizing notes.",
                "files": [
                    {
                        "path": "SKILL.md",
                        "content": _skill_md("notes", "Improved guidance."),
                        "role": "skill",
                    },
                    {"path": "scripts/run.py", "content": "print('v2')\n", "role": "script"},
                    {
                        "path": "agents/openai.yaml",
                        "content": "interface:\n  default_prompt: notes\n",
                        "role": "metadata",
                    },
                ],
                "credential_requirements": [],
                "execution_profile": {},
            },
        )

        updated = await skill_builder_service.confirm_session(db, session, user_id=TEST_USER_ID)
        revisions = await skill_revision_service.list_revisions(
            db,
            skill=updated,
            user_id=TEST_USER_ID,
        )

    assert revisions[0].changelog_summary == (
        "Changed skill package: added 1 file, modified 2 files, removed 1 file."
    )
    assert revisions[0].changelog_items == [
        {"operation": "added", "path": "agents/openai.yaml"},
        {"operation": "modified", "path": "SKILL.md"},
        {"operation": "modified", "path": "scripts/run.py"},
        {"operation": "deleted", "path": "references/old.md"},
    ]
