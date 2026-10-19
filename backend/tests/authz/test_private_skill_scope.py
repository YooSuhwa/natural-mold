"""Private Skill storage and lookup remain isolated across the same user's companies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.config import settings
from app.exceptions import AppError
from app.models.skill import Skill
from app.skills import service
from app.skills.creation import TextSkillCreation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@pytest.fixture(autouse=True)
def isolated_skill_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))


@dataclass(frozen=True, slots=True)
class SkillCase:
    first: Skill
    second: Skill


@pytest.fixture
async def skill_case(db: AsyncSession, private_scopes: PrivateScopes) -> SkillCase:
    creation = TextSkillCreation(
        name="Scoped fixture",
        slug="scoped-fixture",
        description="Fixture",
        content=(
            '---\nname: scoped-fixture\ndescription: "Scope fixture"\n---\n'
            "\nOriginal fixture body\n"
        ),
    )
    first = await service.create_text_skill(db, creation, private_scopes.first)
    second = await service.create_text_skill(db, creation, private_scopes.second)
    await db.commit()
    return SkillCase(first, second)


async def test_skill_writers_and_slug_uniqueness_follow_declared_org(
    skill_case: SkillCase, private_scopes: PrivateScopes
) -> None:
    assert skill_case.first.slug == skill_case.second.slug
    for skill, context in (
        (skill_case.first, private_scopes.first),
        (skill_case.second, private_scopes.second),
    ):
        assert (skill.user_id, skill.org_id, skill.tenant_id) == (
            context.user_id,
            context.org_id,
            context.tenant_id,
        )


async def test_skill_http_lists_and_foreign_id_use_current_org(
    raw_client: AsyncClient, skill_case: SkillCase, private_scopes: PrivateScopes
) -> None:
    raw_client.cookies.set(ACTIVE_ORG_COOKIE, str(private_scopes.second.org_id))
    listed = await raw_client.get("/api/skills")
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [str(skill_case.second.id)]
    foreign = await raw_client.get(f"/api/skills/{skill_case.first.id}")
    assert foreign.status_code == 404, foreign.text


async def test_wrong_org_cannot_mutate_loaded_skill_or_remove_storage(
    db: AsyncSession, skill_case: SkillCase, private_scopes: PrivateScopes
) -> None:
    original = await service.read_text_content(skill_case.first)
    with pytest.raises(AppError) as captured:
        await service.update_text_content(
            db,
            skill=skill_case.first,
            content=original + "Forbidden",
            context=private_scopes.second,
        )
    assert captured.value.status == 404
    with pytest.raises(AppError) as captured:
        await service.delete_skill(db, skill_case.first, private_scopes.second)
    assert captured.value.status == 404
    assert await service.read_text_content(skill_case.first) == original
