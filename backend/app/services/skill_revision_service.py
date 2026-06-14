from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.models.skill_revision import SkillRevision
from app.services.skill_revision_storage import write_skill_revision_snapshot


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


async def _next_revision_number(db: AsyncSession, skill_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.max(SkillRevision.revision_number)).where(SkillRevision.skill_id == skill_id)
    )
    current = result.scalar_one_or_none()
    if current is None:
        return 1
    return int(current) + 1
