from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.models.skill_builder_session import JsonValue
from app.models.skill_revision import SkillRevision
from app.services import skill_revision_retention, skill_revision_service


@dataclass(frozen=True, slots=True)
class MutationRevisionParent:
    parent_revision_id: uuid.UUID | None
    baseline_revision: SkillRevision | None = None


async def create_initial_revision(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    metadata_json: dict[str, JsonValue] | None = None,
) -> SkillRevision:
    return await skill_revision_service.create_revision_for_skill(
        db,
        skill=skill,
        user_id=user_id,
        operation="create",
        metadata_json=metadata_json,
    )


async def prepare_mutation_parent(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
) -> MutationRevisionParent:
    if skill.current_revision_id is not None:
        return MutationRevisionParent(parent_revision_id=skill.current_revision_id)
    baseline = await skill_revision_retention.ensure_baseline_revision(
        db,
        skill=skill,
        user_id=user_id,
        metadata_json={"baseline_source": "first_mutation"},
    )
    if baseline is not None:
        return MutationRevisionParent(parent_revision_id=baseline.id, baseline_revision=baseline)
    revisions = await skill_revision_service.list_revisions(db, skill=skill, user_id=user_id)
    latest = revisions[0] if revisions else None
    if latest is None:
        return MutationRevisionParent(parent_revision_id=None)
    skill.current_revision_id = latest.id
    await db.flush()
    return MutationRevisionParent(parent_revision_id=latest.id)


async def create_manual_revision(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    operation: str,
    parent_revision_id: uuid.UUID | None,
    changed_files: list[JsonValue] | None = None,
    metadata_json: dict[str, JsonValue] | None = None,
) -> SkillRevision:
    return await skill_revision_service.create_revision_for_skill(
        db,
        skill=skill,
        user_id=user_id,
        operation=operation,
        parent_revision_id=parent_revision_id,
        changed_files=changed_files,
        metadata_json=metadata_json,
    )
