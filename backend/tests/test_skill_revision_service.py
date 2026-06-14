from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

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
