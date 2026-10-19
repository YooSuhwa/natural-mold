"""Actual rollback file/row effects after captured private authority becomes invalid."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.exceptions import AppError
from app.models.organization import OrganizationMember
from app.models.skill import Skill
from app.models.skill_revision import SkillRevision
from app.models.user import User
from app.services import skill_revision_service as revisions
from app.skills import service
from app.skills.creation import PackageSkillCreation, TextSkillCreation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
pytest_plugins = ["tests.authz.private_scope_fixture"]
Change = Literal[
    "active", "membership", "account", "foreign_context", "persisted_scope", "revision_parent"
]
Kind = Literal["text", "package"]


def package_bytes(content: str) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", content)
    return buffer.getvalue()


@pytest.mark.parametrize("kind", ["text", "package"])
@pytest.mark.parametrize(
    "change",
    ["active", "membership", "account", "foreign_context", "persisted_scope", "revision_parent"],
)
async def test_rollback_denies_before_files_and_rows(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    kind: Kind,
    change: Change,
) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    original = '---\nname: revision-boundary\ndescription: "Scope fixture"\n---\nORIGINAL\n'
    live = original.replace("ORIGINAL", "LIVE")
    own = private_scopes.first
    match kind:
        case "text":
            skill = await service.create_text_skill(
                db, TextSkillCreation("Revision", "revision", "Fixture", original), own
            )
        case "package":
            skill = await service.create_package_skill(
                db, PackageSkillCreation(package_bytes(original)), own
            )
        case unreachable:
            assert_never(unreachable)
    revision = await revisions.create_revision_for_skill(
        db, skill=skill, user_id=own.user_id, operation="create"
    )
    assert skill.storage_path is not None
    storage_path = tmp_path / skill.storage_path
    skill_file = storage_path / "SKILL.md" if kind == "package" else storage_path
    skill_file.write_text(live)
    await db.commit()
    assert await service.get_skill(db, skill.id, own) is not None
    match change:
        case "active" | "foreign_context":
            pass
        case "membership":
            await db.execute(
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == own.org_id,
                    OrganizationMember.user_id == own.user_id,
                )
                .values(status="removed")
                .execution_options(synchronize_session=False)
            )
        case "account":
            await db.execute(
                update(User)
                .where(User.id == own.user_id)
                .values(is_active=False)
                .execution_options(synchronize_session=False)
            )
        case "persisted_scope":
            other = private_scopes.second
            await db.execute(
                update(Skill)
                .where(Skill.id == skill.id)
                .values(org_id=other.org_id, tenant_id=other.tenant_id)
                .execution_options(synchronize_session=False)
            )
        case "revision_parent":
            other = private_scopes.second
            foreign_skill = Skill(
                user_id=other.user_id,
                org_id=other.org_id,
                tenant_id=other.tenant_id,
                name="Foreign revision parent",
                slug="foreign-revision-parent",
                kind="text",
            )
            db.add(foreign_skill)
            await db.flush()
            await db.execute(
                update(SkillRevision)
                .where(SkillRevision.id == revision.id)
                .values(skill_id=foreign_skill.id)
                .execution_options(synchronize_session=False)
            )
            assert revision.skill_id == skill.id
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    reads: list[str] = []
    read_original = revisions._read_revision_bytes

    def observed_read(object_key: str) -> bytes:
        reads.append(object_key)
        return read_original(object_key)

    monkeypatch.setattr(revisions, "_read_revision_bytes", observed_read)
    before_rows = await db.scalar(select(func.count()).select_from(SkillRevision))
    assert before_rows is not None
    context = private_scopes.second if change == "foreign_context" else own
    denied = False
    try:
        await revisions.rollback_to_revision(
            db, skill=skill, user_id=own.user_id, context=context, revision=revision
        )
        await db.commit()
    except AppError as error:
        assert error.status in {403, 404}
        denied = True
    after_content = skill_file.read_text()
    after_rows = await db.scalar(select(func.count()).select_from(SkillRevision))
    allowed = change == "active"
    assert (denied, len(reads), after_content != live, after_rows) == (
        not allowed,
        1 if allowed else 0,
        allowed,
        before_rows + 1 if allowed else before_rows,
    )
