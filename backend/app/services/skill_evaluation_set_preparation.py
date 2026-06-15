from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.agent import build_skill_builder_chat_model
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationSet
from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_case_generator_llm import (
    ModelBuilder,
    SkillEvaluationCaseGenerationError,
    generate_skill_smoke_eval_payload,
)
from app.services.skill_evaluation_file_adapter import SkillEvaluationFileAdapterError
from app.services.skill_evaluation_preparation_payload import (
    JsonObject,
    evals_from_payload,
    evals_with_preparation_metadata,
    generation_strategy,
    load_embedded_payload,
    payload_description,
    payload_hash,
    payload_name,
)
from app.services.skill_evaluation_service import create_evaluation_set
from app.services.system_credential_resolver import SystemModelNotConfiguredError


class SkillEvaluationPreparationStatus(StrEnum):
    CREATED = "created"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    SKIPPED_NO_EVALS = "skipped_no_evals"
    SKIPPED_NO_SYSTEM_MODEL = "skipped_no_system_model"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SkillEvaluationPreparationResult:
    status: SkillEvaluationPreparationStatus
    evaluation_set_id: uuid.UUID | None
    source_kind: str
    case_count: int
    payload_hash: str | None
    reason: str | None = None


async def prepare_skill_evaluation_set(
    *,
    db: AsyncSession,
    skill: Skill,
    user_id: uuid.UUID,
    source_kind: str,
    allow_llm_generation: bool,
    model_builder: ModelBuilder | None = None,
    marketplace_item_id: uuid.UUID | None = None,
    marketplace_version_id: uuid.UUID | None = None,
) -> SkillEvaluationPreparationResult:
    try:
        embedded = load_embedded_payload(skill)
    except SkillEvaluationFileAdapterError as exc:
        return _failed(source_kind, str(exc))
    if embedded is not None:
        return await _persist_payload(
            db=db,
            skill=skill,
            user_id=user_id,
            source_kind=source_kind,
            payload=embedded,
            marketplace_item_id=marketplace_item_id,
            marketplace_version_id=marketplace_version_id,
            model_name=None,
        )
    if not allow_llm_generation:
        return _skipped(source_kind, SkillEvaluationPreparationStatus.SKIPPED_NO_EVALS)
    return await _prepare_generated_payload(
        db=db,
        skill=skill,
        user_id=user_id,
        original_source_kind=source_kind,
        model_builder=model_builder,
        marketplace_item_id=marketplace_item_id,
        marketplace_version_id=marketplace_version_id,
    )


async def _prepare_generated_payload(
    *,
    db: AsyncSession,
    skill: Skill,
    user_id: uuid.UUID,
    original_source_kind: str,
    model_builder: ModelBuilder | None,
    marketplace_item_id: uuid.UUID | None,
    marketplace_version_id: uuid.UUID | None,
) -> SkillEvaluationPreparationResult:
    try:
        generated = await generate_skill_smoke_eval_payload(
            db,
            skill=skill,
            model_builder=model_builder or build_skill_builder_chat_model,
        )
    except SystemModelNotConfiguredError:
        return _skipped(
            original_source_kind,
            SkillEvaluationPreparationStatus.SKIPPED_NO_SYSTEM_MODEL,
        )
    except SkillEvaluationCaseGenerationError as exc:
        return _failed(original_source_kind, str(exc))
    return await _persist_payload(
        db=db,
        skill=skill,
        user_id=user_id,
        source_kind="llm_generated",
        payload=generated.payload,
        marketplace_item_id=marketplace_item_id,
        marketplace_version_id=marketplace_version_id,
        model_name=generated.model_name,
    )


async def _persist_payload(
    *,
    db: AsyncSession,
    skill: Skill,
    user_id: uuid.UUID,
    source_kind: str,
    payload: JsonObject,
    marketplace_item_id: uuid.UUID | None,
    marketplace_version_id: uuid.UUID | None,
    model_name: str | None,
) -> SkillEvaluationPreparationResult:
    evals = evals_from_payload(payload)
    payload_hash_value = payload_hash(
        source_kind=source_kind,
        evals=evals,
        marketplace_item_id=marketplace_item_id,
        marketplace_version_id=marketplace_version_id,
    )
    duplicate = await _has_duplicate(
        db,
        skill=skill,
        user_id=user_id,
        payload_hash_value=payload_hash_value,
    )
    if duplicate:
        return SkillEvaluationPreparationResult(
            status=SkillEvaluationPreparationStatus.SKIPPED_DUPLICATE,
            evaluation_set_id=None,
            source_kind=source_kind,
            case_count=len(evals),
            payload_hash=payload_hash_value,
            reason="duplicate_payload",
        )
    evaluation_set = await create_evaluation_set(
        db,
        user_id=user_id,
        skill=skill,
        name=payload_name(payload, source_kind),
        description=payload_description(payload),
        source_kind=source_kind,
        generation_strategy=generation_strategy(
            source_kind=source_kind,
            payload_hash_value=payload_hash_value,
            marketplace_item_id=marketplace_item_id,
            marketplace_version_id=marketplace_version_id,
            model_name=model_name,
        ),
        evals=evals_with_preparation_metadata(
            evals=evals,
            payload_hash_value=payload_hash_value,
            source_kind=source_kind,
        ),
    )
    return SkillEvaluationPreparationResult(
        status=SkillEvaluationPreparationStatus.CREATED,
        evaluation_set_id=evaluation_set.id,
        source_kind=source_kind,
        case_count=len(evals),
        payload_hash=payload_hash_value,
    )


async def _has_duplicate(
    db: AsyncSession,
    *,
    skill: Skill,
    user_id: uuid.UUID,
    payload_hash_value: str,
) -> bool:
    result = await db.execute(
        select(SkillEvaluationSet).where(
            SkillEvaluationSet.skill_id == skill.id,
            SkillEvaluationSet.user_id == user_id,
        )
    )
    for evaluation_set in result.scalars():
        strategy = _json_strategy(evaluation_set.generation_strategy)
        if strategy.get("payload_hash") == payload_hash_value:
            return True
    return False


def _json_strategy(value: dict[str, JsonValue] | None) -> dict[str, JsonValue]:
    return value or {}


def _skipped(
    source_kind: str,
    status: SkillEvaluationPreparationStatus,
) -> SkillEvaluationPreparationResult:
    return SkillEvaluationPreparationResult(
        status=status,
        evaluation_set_id=None,
        source_kind=source_kind,
        case_count=0,
        payload_hash=None,
    )


def _failed(source_kind: str, reason: str) -> SkillEvaluationPreparationResult:
    return SkillEvaluationPreparationResult(
        status=SkillEvaluationPreparationStatus.FAILED,
        evaluation_set_id=None,
        source_kind=source_kind,
        case_count=0,
        payload_hash=None,
        reason=reason,
    )
