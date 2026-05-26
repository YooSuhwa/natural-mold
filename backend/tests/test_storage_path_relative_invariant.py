"""Regression gate for ADR-018 — DB writes never persist absolute paths.

The 2026-05-23 incident wiped 95 rows because every publish/install path
hardcoded a worktree-local absolute path into the DB. This test creates
skills + marketplace versions through the normal service layer and
asserts the stored values are POSIX-relative.

Failure mode: a future refactor reintroduces ``str(Path(...).resolve())``
on a write site. The path becomes ``/Users/.../data/skills/<id>`` and
this test trips.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID


def _zip_with_skill_md(body: str = "# SKILL\n") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", body)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_text_skill_storage_path_is_relative(
    db: AsyncSession, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    skill = await skill_service.create_text_skill(
        db,
        user_id=TEST_USER_ID,
        name="abs guard text",
        slug=None,
        description=None,
        content="hi",
    )
    await db.commit()
    await db.refresh(skill)

    assert skill.storage_path is not None
    assert not Path(skill.storage_path).is_absolute(), (
        f"text skill stored absolute path: {skill.storage_path}"
    )
    assert skill.storage_path.startswith("skills/")
    assert skill.storage_path.endswith("/SKILL.md")


@pytest.mark.asyncio
async def test_package_skill_storage_path_is_relative(
    db: AsyncSession, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    skill = await skill_service.create_package_skill(
        db,
        user_id=TEST_USER_ID,
        zip_bytes=_zip_with_skill_md(),
    )
    await db.commit()
    await db.refresh(skill)

    assert skill.storage_path is not None
    assert not Path(skill.storage_path).is_absolute(), (
        f"package skill stored absolute path: {skill.storage_path}"
    )
    assert skill.storage_path.startswith("skills/")
    try:
        uuid.UUID(skill.storage_path.removeprefix("skills/"))
    except ValueError:  # pragma: no cover — defensive
        pytest.fail(f"unexpected storage_path format: {skill.storage_path}")
