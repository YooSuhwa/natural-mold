from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.agent import (
    SkillBuilderChatModel,
    build_skill_builder_chat_model,
)
from app.config import settings
from app.dependencies import CurrentUser
from app.models.marketplace import MarketplaceInstallation
from app.models.skill import Skill
from app.services.skill_evaluation_case_generator_llm import ModelBuilder
from app.services.skill_evaluation_file_adapter import SkillEvaluationFileAdapterError
from app.services.skill_evaluation_preparation_payload import load_embedded_payload
from app.services.skill_evaluation_set_preparation import (
    SkillEvaluationPreparationResult,
    SkillEvaluationPreparationStatus,
    prepare_skill_evaluation_set,
)
from app.services.system_credential_resolver import SystemModelNotConfiguredError


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
    model_builder = await _model_builder_for_marketplace_prepare(db, skill)
    try:
        return await prepare_skill_evaluation_set(
            db=db,
            skill=skill,
            user_id=user.id,
            source_kind="marketplace_import",
            allow_llm_generation=settings.skill_evaluation_enabled,
            model_builder=model_builder,
            marketplace_item_id=installation.item_id,
            marketplace_version_id=installation.version_id,
        )
    except Exception as exc:  # noqa: BLE001 - install-side prep is non-fatal
        await db.rollback()
        return SkillEvaluationPreparationResult(
            status=SkillEvaluationPreparationStatus.FAILED,
            evaluation_set_id=None,
            source_kind="marketplace_import",
            case_count=0,
            payload_hash=None,
            reason=f"unexpected_{exc.__class__.__name__}",
        )


async def _model_builder_for_marketplace_prepare(
    db: AsyncSession,
    skill: Skill,
) -> ModelBuilder | None:
    if not settings.skill_evaluation_enabled:
        return None
    try:
        if load_embedded_payload(skill) is not None:
            return None
    except SkillEvaluationFileAdapterError:
        return None
    try:
        built_model = await build_skill_builder_chat_model(db)
    except SystemModelNotConfiguredError:
        await db.rollback()
        return _missing_system_model_builder
    except Exception:  # noqa: BLE001 - converted to FAILED by caller
        await db.rollback()
        raise
    await db.commit()

    async def model_builder(_db: AsyncSession) -> SkillBuilderChatModel:
        return built_model

    return model_builder


async def _missing_system_model_builder(_db: AsyncSession) -> SkillBuilderChatModel:
    raise SystemModelNotConfiguredError("text_primary")
