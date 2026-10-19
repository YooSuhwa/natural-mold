"""Skill filesystem writers must revalidate captured authority before effects."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import AppError
from app.models.organization import OrganizationMember
from app.models.skill import Skill
from app.skills import service
from app.skills.creation import TextSkillCreation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_skill_scope import SkillCase

pytest_plugins = ["tests.authz.test_private_skill_scope"]


async def _suspend_membership(db: AsyncSession, scopes: PrivateScopes) -> None:
    own = scopes.first
    await db.execute(
        update(OrganizationMember)
        .where(OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id)
        .values(status="suspended")
    )
    await db.commit()
    assert (
        await db.scalar(
            select(OrganizationMember.status).where(
                OrganizationMember.org_id == own.org_id, OrganizationMember.user_id == own.user_id
            )
        )
        == "suspended"
    )


async def test_skill_creation_revalidates_before_files_and_durable_row(
    db: AsyncSession, private_scopes: PrivateScopes, skill_case: SkillCase
) -> None:
    assert skill_case.first.org_id == private_scopes.first.org_id
    # I-08: new skills are written to the company folder.
    root = Path(settings.data_root) / "tenants" / str(private_scopes.first.tenant_id) / "skills"
    before_files = sorted(path.name for path in root.iterdir())
    before_rows = await db.scalar(select(func.count()).select_from(Skill))
    await _suspend_membership(db, private_scopes)
    denied = False
    try:
        await service.create_text_skill(
            db,
            TextSkillCreation(
                "Revoked fixture",
                "revoked-fixture",
                "Fixture",
                '---\nname: revoked-fixture\ndescription: "Fixture"\n---\nB06_DUMMY_ONLY\n',
            ),
            private_scopes.first,
        )
        await db.commit()
    except AppError:
        denied = True
    after_files = sorted(path.name for path in root.iterdir())
    after_rows = await db.scalar(select(func.count()).select_from(Skill))
    assert denied, (
        f"Suspended Skill creator accepted: denied={denied}, "
        f"dirs_before={len(before_files)}, dirs_after={len(after_files)}, "
        f"rows_before={before_rows}, rows_after={after_rows}"
    )
    assert after_files == before_files
    assert after_rows == before_rows


async def test_skill_edit_revalidates_before_files_and_durable_row(
    db: AsyncSession, private_scopes: PrivateScopes, skill_case: SkillCase
) -> None:
    skill = skill_case.first
    before = await service.read_text_content(skill)
    with pytest.raises(AppError):
        await service.update_text_content(
            db, skill=skill, content=before + "Foreign", context=private_scopes.second
        )
    assert await service.read_text_content(skill) == before
    await _suspend_membership(db, private_scopes)
    denied = False
    try:
        await service.update_text_content(
            db, skill=skill, content=before + "B06_DUMMY_REVOKED_EDIT", context=private_scopes.first
        )
        await db.commit()
    except AppError:
        denied = True
    after = await service.read_text_content(skill)
    assert denied, (
        f"Suspended Skill editor accepted: denied={denied}, file_changed={after != before}"
    )
    assert after == before
