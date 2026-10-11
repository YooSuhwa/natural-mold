from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.skills import service as skill_service
from app.skills.creation import PackageSkillCreation
from tests.authz.unit_scope_fixture import create_test_package_skill, unit_context
from tests.conftest import TEST_USER_ID


def _skill_md(name: str = "demo") -> str:
    return f'---\nname: {name}\ndescription: "demo skill"\nversion: "1.0.0"\n---\n\n# Demo body\n'


def _zip_with(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_set_skill_file_recomputes_package_content_hash(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await create_test_package_skill(
            db,
            PackageSkillCreation(
                zip_bytes=_zip_with({"SKILL.md": _skill_md("pkg"), "scripts/run.py": "print(1)\n"})
            ),
            unit_context(TEST_USER_ID),
        )
        await db.commit()
        old_hash = skill.content_hash

        await skill_service.set_skill_file(
            db,
            skill=skill,
            rel_path="scripts/run.py",
            content=b"print(2)\n",
        )
        await db.commit()
        changed_hash = skill.content_hash

        await skill_service.set_skill_file(
            db,
            skill=skill,
            rel_path="scripts/run.py",
            content=b"print(2)\n",
        )
        await db.commit()

    assert changed_hash is not None
    assert old_hash != changed_hash
    assert skill.content_hash == changed_hash


@pytest.mark.asyncio
async def test_delete_skill_file_recomputes_package_content_hash(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await create_test_package_skill(
            db,
            PackageSkillCreation(
                zip_bytes=_zip_with(
                    {
                        "SKILL.md": _skill_md("pkg-delete"),
                        "scripts/run.py": "print(1)\n",
                        "notes.md": "temporary\n",
                    }
                )
            ),
            unit_context(TEST_USER_ID),
        )
        await db.commit()
        old_hash = skill.content_hash

        await skill_service.delete_skill_file(db, skill=skill, rel_path="notes.md")
        await db.commit()

    assert skill.content_hash is not None
    assert skill.content_hash != old_hash
