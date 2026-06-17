from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.services.skill_evaluation_case_generator_llm import ModelBuilder
from app.services.skill_evaluation_set_preparation import (
    SkillEvaluationPreparationResult,
    SkillEvaluationPreparationStatus,
    prepare_skill_evaluation_set,
)

logger = logging.getLogger(__name__)


async def prepare_skill_evaluation_set_best_effort(
    *,
    db: AsyncSession,
    skill: Skill,
    user_id: uuid.UUID,
    source_kind: str,
    allow_llm_generation: bool,
    force: bool = False,
    model_builder: ModelBuilder | None = None,
    marketplace_item_id: uuid.UUID | None = None,
    marketplace_version_id: uuid.UUID | None = None,
) -> SkillEvaluationPreparationResult:
    try:
        async with db.begin_nested():
            return await prepare_skill_evaluation_set(
                db=db,
                skill=skill,
                user_id=user_id,
                source_kind=source_kind,
                allow_llm_generation=allow_llm_generation,
                force=force,
                model_builder=model_builder,
                marketplace_item_id=marketplace_item_id,
                marketplace_version_id=marketplace_version_id,
            )
    except Exception as exc:  # noqa: BLE001 - auto-prepare must not abort install/upload
        logger.warning(
            "skill_evaluation_auto_prepare_failed skill_id=%s source_kind=%s",
            skill.id,
            source_kind,
            exc_info=True,
        )
        return SkillEvaluationPreparationResult(
            status=SkillEvaluationPreparationStatus.FAILED,
            evaluation_set_id=None,
            source_kind=source_kind,
            case_count=0,
            payload_hash=None,
            reason=f"unexpected_{exc.__class__.__name__}",
        )
