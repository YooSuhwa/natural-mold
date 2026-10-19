"""Persisted private rollback targets precede snapshot access and file mutations."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.authz.private_actor import require_private_actor
from app.authz.scoping import private_scoped
from app.authz.skill_binding_scope import private_skill_child_predicate
from app.error_codes import skill_not_found
from app.models.skill import Skill
from app.models.skill_revision import SkillRevision


@dataclass(frozen=True, slots=True)
class RevisionRollbackTarget:
    skill_id: UUID
    revision_id: UUID
    context: OrgContext


async def load_private_rollback_target(
    db: AsyncSession, target: RevisionRollbackTarget
) -> tuple[Skill, SkillRevision]:
    await require_private_actor(db, target.context)
    skill = await db.scalar(
        private_scoped(select(Skill).where(Skill.id == target.skill_id), Skill, target.context)
        .with_for_update()
        .execution_options(populate_existing=True, autoflush=False)
    )
    if skill is None:
        raise skill_not_found()
    revision = await db.scalar(
        select(SkillRevision)
        .where(
            SkillRevision.id == target.revision_id,
            SkillRevision.skill_id == skill.id,
            private_skill_child_predicate(SkillRevision, target.context),
        )
        .execution_options(populate_existing=True, autoflush=False)
    )
    if revision is None:
        raise skill_not_found()
    return skill, revision
