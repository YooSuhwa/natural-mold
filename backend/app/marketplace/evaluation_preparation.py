from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser
from app.models.marketplace import MarketplaceInstallation
from app.models.skill import Skill
from app.services.skill_evaluation_set_preparation import (
    SkillEvaluationPreparationResult,
    prepare_skill_evaluation_set,
)


async def prepare_installed_skill_evaluation_set(
    db: AsyncSession,
    *,
    installation: MarketplaceInstallation,
    user: CurrentUser,
) -> SkillEvaluationPreparationResult | None:
    if installation.resource_type != "skill" or installation.installed_skill_id is None:
        return None
    skill = await db.get(Skill, installation.installed_skill_id)
    if skill is None:
        return None
    return await prepare_skill_evaluation_set(
        db=db,
        skill=skill,
        user_id=user.id,
        source_kind="marketplace_import",
        allow_llm_generation=True,
        marketplace_item_id=installation.item_id,
        marketplace_version_id=installation.version_id,
    )
