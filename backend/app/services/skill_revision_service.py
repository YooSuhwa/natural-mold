from __future__ import annotations

import asyncio
import shutil
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill import Skill
from app.models.skill_revision import SkillRevision
from app.services.skill_revision_storage import write_skill_revision_snapshot
from app.skills import service as skill_service
from app.skills.package_metadata import refresh_package_metadata, sync_frontmatter
from app.skills.packager import extract_package
from app.storage.paths import resolve_data_path


async def create_revision_for_skill(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    operation: str,
    source_session_id: uuid.UUID | None = None,
    parent_revision_id: uuid.UUID | None = None,
    restored_from_revision_id: uuid.UUID | None = None,
    changed_files: list[Any] | None = None,
    changelog_summary: str | None = None,
    changelog_items: list[Any] | None = None,
    compatibility_result: dict[str, Any] | None = None,
    evaluation_summary: dict[str, Any] | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> SkillRevision:
    revision_number = await _next_revision_number(db, skill.id)
    snapshot = await write_skill_revision_snapshot(skill, revision_number=revision_number)
    revision = SkillRevision(
        skill_id=skill.id,
        user_id=user_id,
        source_session_id=source_session_id,
        parent_revision_id=parent_revision_id,
        restored_from_revision_id=restored_from_revision_id,
        revision_number=revision_number,
        operation=operation,
        skill_version=skill.version,
        content_hash=skill.content_hash,
        storage_provider=snapshot.storage_provider,
        object_key=snapshot.object_key,
        size_bytes=snapshot.size_bytes,
        file_count=snapshot.file_count,
        changed_files=changed_files,
        changelog_summary=changelog_summary,
        changelog_items=changelog_items,
        compatibility_result=compatibility_result,
        evaluation_summary=evaluation_summary,
        metadata_json=metadata_json or {},
    )
    db.add(revision)
    await db.flush()
    skill.current_revision_id = revision.id
    await db.flush()
    return revision


async def list_revisions(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
) -> list[SkillRevision]:
    if skill.user_id != user_id:
        return []
    result = await db.execute(
        select(SkillRevision)
        .where(SkillRevision.skill_id == skill.id, SkillRevision.user_id == user_id)
        .order_by(desc(SkillRevision.revision_number))
    )
    return list(result.scalars().all())


async def get_revision(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    revision_id: uuid.UUID,
) -> SkillRevision | None:
    if skill.user_id != user_id:
        return None
    result = await db.execute(
        select(SkillRevision).where(
            SkillRevision.id == revision_id,
            SkillRevision.skill_id == skill.id,
            SkillRevision.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def rollback_to_revision(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    revision: SkillRevision,
    changelog_summary: str | None = None,
) -> SkillRevision:
    if skill.user_id != user_id or revision.skill_id != skill.id:
        raise SkillRevisionNotFound("revision not found")
    parent_revision_id = skill.current_revision_id
    zip_bytes = await asyncio.to_thread(_read_revision_bytes, revision.object_key)
    if skill.kind == "text":
        content = await asyncio.to_thread(_read_skill_md, zip_bytes)
        await skill_service.update_text_content(db, skill=skill, content=content)
    else:
        if not skill.storage_path:
            raise SkillRevisionRollbackUnsupported("package skill has no storage path")
        await asyncio.to_thread(_replace_package_files, skill.storage_path, zip_bytes)
        refresh_package_metadata(skill)
        sync_frontmatter(skill, skill_service.get_file_bytes(skill, "SKILL.md"))
        await db.flush()
    return await create_revision_for_skill(
        db,
        skill=skill,
        user_id=user_id,
        operation="rollback",
        parent_revision_id=parent_revision_id,
        restored_from_revision_id=revision.id,
        changelog_summary=changelog_summary,
    )


async def _next_revision_number(db: AsyncSession, skill_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.max(SkillRevision.revision_number)).where(SkillRevision.skill_id == skill_id)
    )
    current = result.scalar_one_or_none()
    if current is None:
        return 1
    return int(current) + 1


class SkillRevisionNotFound(LookupError):
    pass


class SkillRevisionRollbackUnsupported(RuntimeError):
    pass


def _read_revision_bytes(object_key: str) -> bytes:
    path = (Path(settings.data_root) / object_key).resolve()
    root = Path(settings.data_root).resolve()
    if not path.is_relative_to(root):
        raise ValueError("skill revision path escapes data root")
    return path.read_bytes()


def _read_skill_md(zip_bytes: bytes) -> str:
    with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
        return archive.read("SKILL.md").decode("utf-8")


def _replace_package_files(storage_path: str, zip_bytes: bytes) -> None:
    root = resolve_data_path(storage_path)
    with TemporaryDirectory() as temp_dir:
        extracted = Path(temp_dir) / "skill"
        extract_package(zip_bytes, extracted)
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(extracted, root)
