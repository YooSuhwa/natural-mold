from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.models.skill_builder_session import JsonValue, SkillBuilderSession
from app.models.skill_revision import SkillRevision
from app.services import skill_revision_service
from app.services.skill_revision_storage import delete_skill_revision_snapshot

MIN_REVISIONS_TO_KEEP: Final = 20
REVISION_RETENTION_DAYS: Final = 180
ACTIVE_BUILDER_STATUSES: Final = ("collecting", "drafting", "review", "confirming")


async def ensure_baseline_revision(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    metadata_json: dict[str, JsonValue] | None = None,
) -> SkillRevision | None:
    result = await db.execute(
        select(SkillRevision.id).where(SkillRevision.skill_id == skill.id).limit(1)
    )
    if result.scalar_one_or_none() is not None:
        return None
    metadata: dict[str, JsonValue] = {"baseline": True}
    if metadata_json:
        metadata.update(metadata_json)
    return await skill_revision_service.create_revision_for_skill(
        db,
        skill=skill,
        user_id=user_id,
        operation="create",
        metadata_json=metadata,
    )


async def backfill_missing_revisions(
    db: AsyncSession,
    *,
    batch_size: int = 100,
) -> int:
    missing_revision = ~select(SkillRevision.id).where(SkillRevision.skill_id == Skill.id).exists()
    result = await db.execute(
        select(Skill).where(missing_revision).order_by(Skill.created_at, Skill.id).limit(batch_size)
    )
    count = 0
    for skill in result.scalars().all():
        revision = await ensure_baseline_revision(
            db,
            skill=skill,
            user_id=skill.user_id,
            metadata_json={"backfilled": True},
        )
        if revision is not None:
            count += 1
    return count


async def count_skills_missing_revisions(db: AsyncSession) -> int:
    missing_revision = ~select(SkillRevision.id).where(SkillRevision.skill_id == Skill.id).exists()
    result = await db.execute(select(func.count()).select_from(Skill).where(missing_revision))
    return int(result.scalar_one())


async def prune_revisions_for_skill(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> list[SkillRevision]:
    if skill.user_id != user_id:
        return []
    # limit=None 필수 — 기본 100 창만 보면 창 밖 리비전이 영구히 prune에서
    # 빠져 스냅샷 디스크가 샌다 (R5).
    revisions = await skill_revision_service.list_revisions(
        db, skill=skill, user_id=user_id, limit=None
    )
    if len(revisions) <= MIN_REVISIONS_TO_KEEP:
        return []

    protected_ids = await _protected_revision_ids(db, skill=skill, revisions=revisions)
    cutoff = (now or _now()) - timedelta(days=REVISION_RETENTION_DAYS)
    pruned: list[SkillRevision] = []
    for revision in revisions[MIN_REVISIONS_TO_KEEP:]:
        if revision.id in protected_ids:
            continue
        if revision.created_at >= cutoff:
            continue
        if _snapshot_pruned(revision):
            continue
        await delete_skill_revision_snapshot(revision.object_key)
        revision.metadata_json = {
            **(revision.metadata_json or {}),
            "snapshot_pruned": True,
            "snapshot_pruned_at": (now or _now()).isoformat(),
        }
        pruned.append(revision)
    await db.flush()
    return pruned


async def _protected_revision_ids(
    db: AsyncSession,
    *,
    skill: Skill,
    revisions: list[SkillRevision],
) -> set[uuid.UUID]:
    protected: set[uuid.UUID] = set()
    if skill.current_revision_id is not None:
        protected.add(skill.current_revision_id)
    active_hashes = await _active_builder_base_hashes(db, skill)
    for revision in revisions:
        if _marketplace_published(revision):
            protected.add(revision.id)
        if revision.content_hash is not None and revision.content_hash in active_hashes:
            protected.add(revision.id)
    return protected


async def _active_builder_base_hashes(db: AsyncSession, skill: Skill) -> set[str]:
    result = await db.execute(
        select(SkillBuilderSession.base_content_hash).where(
            SkillBuilderSession.source_skill_id == skill.id,
            SkillBuilderSession.mode == "improve",
            SkillBuilderSession.status.in_(ACTIVE_BUILDER_STATUSES),
            SkillBuilderSession.base_content_hash.is_not(None),
        )
    )
    return {value for value in result.scalars().all() if value is not None}


def _marketplace_published(revision: SkillRevision) -> bool:
    metadata = revision.metadata_json or {}
    return bool(metadata.get("marketplace_published") or metadata.get("marketplace_version_id"))


def _snapshot_pruned(revision: SkillRevision) -> bool:
    return bool((revision.metadata_json or {}).get("snapshot_pruned"))


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
