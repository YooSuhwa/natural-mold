from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import skill_revision_service
from app.services.skill_revision_storage import write_skill_revision_snapshot
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID


def _skill_content(name: str = "revision-demo") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when testing revision snapshots."\n'
        "---\n\n"
        "Use when testing revision snapshots.\n"
    )


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def _zip_names(zip_path) -> set[str]:
    with zipfile.ZipFile(zip_path) as archive:
        return set(archive.namelist())


@pytest.mark.asyncio
async def test_write_text_skill_revision_snapshot(db: AsyncSession, tmp_path) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Revision Demo",
            slug="revision-demo",
            description="Use when testing revision snapshots.",
            content=_skill_content(),
        )
        snapshot = await write_skill_revision_snapshot(skill, revision_number=1)

    assert snapshot.object_key.endswith("/r1/skill.zip")
    assert snapshot.file_count == 1
    assert snapshot.size_bytes > 0
    assert snapshot.path.is_file()
    assert _zip_names(snapshot.path) == {"SKILL.md"}


@pytest.mark.asyncio
async def test_write_package_skill_revision_snapshot(db: AsyncSession, tmp_path) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_bytes(
                {
                    "SKILL.md": _skill_content("revision-package"),
                    "scripts/run.py": "print('ok')\n",
                }
            ),
        )
        snapshot = await write_skill_revision_snapshot(skill, revision_number=2)

    assert snapshot.object_key.endswith("/r2/skill.zip")
    assert snapshot.file_count == 2
    assert _zip_names(snapshot.path) == {"SKILL.md", "scripts/run.py"}


@pytest.mark.asyncio
async def test_create_revision_for_skill_updates_current_pointer(
    db: AsyncSession,
    tmp_path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Revision Demo",
            slug="revision-demo",
            description="Use when testing revision snapshots.",
            content=_skill_content(),
        )
        revision = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
            changelog_summary="Initial snapshot",
        )
        await db.commit()

    assert revision.revision_number == 1
    assert revision.content_hash == skill.content_hash
    assert revision.file_count == 1
    assert revision.changelog_summary == "Initial snapshot"
    assert skill.current_revision_id == revision.id


@pytest.mark.asyncio
async def test_list_revisions_returns_newest_first(db: AsyncSession, tmp_path) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Revision Demo",
            slug="revision-demo",
            description="Use when testing revision snapshots.",
            content=_skill_content(),
        )
        first = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )
        second = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="manual_content_update",
        )

    revisions = await skill_revision_service.list_revisions(db, skill=skill, user_id=TEST_USER_ID)

    assert [revision.id for revision in revisions] == [second.id, first.id]


@pytest.mark.asyncio
async def test_get_revision_is_user_scoped(db: AsyncSession, tmp_path) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Revision Demo",
            slug="revision-demo",
            description="Use when testing revision snapshots.",
            content=_skill_content(),
        )
        revision = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )

    assert await skill_revision_service.get_revision(
        db,
        skill=skill,
        user_id=TEST_USER_ID,
        revision_id=revision.id,
    ) == revision
    assert (
        await skill_revision_service.get_revision(
            db,
            skill=skill,
            user_id=revision.id,
            revision_id=revision.id,
        )
        is None
    )


@pytest.mark.asyncio
async def test_rollback_to_revision_restores_text_skill_and_creates_revision(
    db: AsyncSession,
    tmp_path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_text_skill(
            db,
            user_id=TEST_USER_ID,
            name="Revision Demo",
            slug="revision-demo",
            description="Use when testing revision snapshots.",
            content=_skill_content(),
        )
        first = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )
        await skill_service.update_text_content(
            db,
            skill=skill,
            content=_skill_content("revision-updated"),
        )
        second = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="manual_content_update",
        )

        rollback = await skill_revision_service.rollback_to_revision(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            revision=first,
            changelog_summary="Rollback to initial version",
        )
        restored = await skill_service.read_text_content(skill)

    assert second.id != rollback.id
    assert rollback.revision_number == 3
    assert rollback.operation == "rollback"
    assert rollback.restored_from_revision_id == first.id
    assert skill.current_revision_id == rollback.id
    assert "name: revision-demo" in restored


@pytest.mark.asyncio
async def test_rollback_to_revision_restores_package_skill_and_hash(
    db: AsyncSession,
    tmp_path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await skill_service.create_package_skill(
            db,
            user_id=TEST_USER_ID,
            zip_bytes=_zip_bytes(
                {
                    "SKILL.md": _skill_content("revision-package"),
                    "scripts/run.py": "print('v1')\n",
                }
            ),
        )
        first = await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="create",
        )
        first_hash = skill.content_hash
        await skill_service.set_skill_file(
            db,
            skill=skill,
            rel_path="scripts/run.py",
            content=b"print('v2')\n",
        )
        await skill_revision_service.create_revision_for_skill(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            operation="manual_file_update",
        )

        rollback = await skill_revision_service.rollback_to_revision(
            db,
            skill=skill,
            user_id=TEST_USER_ID,
            revision=first,
        )
        restored = skill_service.get_file_bytes(skill, "scripts/run.py")

    assert rollback.operation == "rollback"
    assert skill.content_hash == first_hash
    assert restored == b"print('v1')\n"
