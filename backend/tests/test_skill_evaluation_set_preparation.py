from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.agent import SkillBuilderChatModel
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_case_generator_llm import ModelBuilder
from app.services.skill_evaluation_set_preparation import (
    SkillEvaluationPreparationStatus,
    prepare_skill_evaluation_set,
)
from app.storage.paths import ensure_relative
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio
_DATA_ROOT_PATCH = "app.storage.paths.settings.data_root"


async def test_prepare_imports_embedded_claude_evals_for_package_skill(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a package skill containing Claude-style evals/evals.json.
    skill = await _package_skill(
        db,
        tmp_path,
        eval_payload={
            "skill_name": "meeting-notes",
            "evals": [
                {
                    "id": "case-001",
                    "prompt": "Extract action items.",
                    "expected_output": "Action item table.",
                    "files": ["inputs/note.md"],
                    "expectations": ["Includes owners"],
                }
            ],
        },
    )

    # When: preparation runs without LLM fallback.
    with patch(_DATA_ROOT_PATCH, str(tmp_path)):
        result = await prepare_skill_evaluation_set(
            db=db,
            skill=skill,
            user_id=TEST_USER_ID,
            source_kind="package_import",
            allow_llm_generation=False,
        )

    # Then: an evaluation set is created with normalized Claude metadata.
    assert result.status is SkillEvaluationPreparationStatus.CREATED
    evaluation_set = await _evaluation_set(db, result.evaluation_set_id)
    assert evaluation_set is not None
    assert evaluation_set.source_kind == "package_import"
    assert evaluation_set.name == "meeting-notes imported evals"
    assert evaluation_set.evals[0]["input"] == "Extract action items."
    assert evaluation_set.evals[0]["metadata"]["source_schema"] == "claude_skill_creator"
    assert evaluation_set.generation_strategy["payload_hash"] == result.payload_hash


async def test_prepare_imports_embedded_moldy_evals_for_package_skill(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a package skill containing Moldy-style evals/evals.json.
    skill = await _package_skill(
        db,
        tmp_path,
        eval_payload={
            "name": "Smoke",
            "description": "Imported smoke cases",
            "evals": [{"input": "Summarize.", "expected": "Summary."}],
        },
    )

    # When: preparation runs.
    with patch(_DATA_ROOT_PATCH, str(tmp_path)):
        result = await prepare_skill_evaluation_set(
            db=db,
            skill=skill,
            user_id=TEST_USER_ID,
            source_kind="package_import",
            allow_llm_generation=False,
        )

    # Then: the Moldy eval file is persisted as a SkillEvaluationSet.
    evaluation_set = await _evaluation_set(db, result.evaluation_set_id)
    assert result.status is SkillEvaluationPreparationStatus.CREATED
    assert evaluation_set is not None
    assert evaluation_set.name == "Smoke"
    assert evaluation_set.description == "Imported smoke cases"
    assert evaluation_set.evals[0]["metadata"]["source_schema"] == "moldy"


async def test_prepare_skips_duplicate_payload(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: the same package eval payload is prepared twice.
    skill = await _package_skill(
        db,
        tmp_path,
        eval_payload={"evals": [{"input": "Classify.", "expected": "Label."}]},
    )

    # When: preparation runs twice for the same source.
    with patch(_DATA_ROOT_PATCH, str(tmp_path)):
        first = await prepare_skill_evaluation_set(
            db=db,
            skill=skill,
            user_id=TEST_USER_ID,
            source_kind="package_import",
            allow_llm_generation=False,
        )
        second = await prepare_skill_evaluation_set(
            db=db,
            skill=skill,
            user_id=TEST_USER_ID,
            source_kind="package_import",
            allow_llm_generation=False,
        )

    # Then: the second attempt reports a duplicate and does not create a set.
    assert first.status is SkillEvaluationPreparationStatus.CREATED
    assert second.status is SkillEvaluationPreparationStatus.SKIPPED_DUPLICATE
    assert await _evaluation_set_count(db, skill) == 1


async def test_prepare_skips_missing_evals_when_llm_generation_disabled(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a package skill without evals/evals.json.
    skill = await _package_skill(db, tmp_path, eval_payload=None)

    # When: preparation runs with LLM generation disabled.
    with patch(_DATA_ROOT_PATCH, str(tmp_path)):
        result = await prepare_skill_evaluation_set(
            db=db,
            skill=skill,
            user_id=TEST_USER_ID,
            source_kind="package_import",
            allow_llm_generation=False,
        )

    # Then: preparation skips without creating a set.
    assert result.status is SkillEvaluationPreparationStatus.SKIPPED_NO_EVALS
    assert result.evaluation_set_id is None
    assert await _evaluation_set_count(db, skill) == 0


async def test_prepare_generates_smoke_evals_when_embedded_file_missing(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a package skill without evals and a fake smoke-case model.
    skill = await _package_skill(db, tmp_path, eval_payload=None)
    model_builder = _fake_builder_model(
        {
            "evals": [
                {
                    "input": "Draft a reply.",
                    "expected": "A concise reply.",
                    "metadata": {"expectations": ["Polite tone"]},
                }
            ]
        }
    )

    # When: preparation runs with LLM fallback enabled.
    with patch(_DATA_ROOT_PATCH, str(tmp_path)):
        result = await prepare_skill_evaluation_set(
            db=db,
            skill=skill,
            user_id=TEST_USER_ID,
            source_kind="package_import",
            allow_llm_generation=True,
            model_builder=model_builder,
        )

    # Then: generated smoke cases are persisted.
    evaluation_set = await _evaluation_set(db, result.evaluation_set_id)
    assert result.status is SkillEvaluationPreparationStatus.CREATED
    assert evaluation_set is not None
    assert evaluation_set.source_kind == "llm_generated"
    assert evaluation_set.evals[0]["metadata"]["generated"] is True
    assert evaluation_set.generation_strategy["model_name"] == "fake-smoke-model"


async def test_prepare_does_not_create_run(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a package skill with importable evals.
    skill = await _package_skill(
        db,
        tmp_path,
        eval_payload={"evals": [{"input": "Summarize.", "expected": "Summary."}]},
    )

    # When: preparation creates an evaluation set.
    with patch(_DATA_ROOT_PATCH, str(tmp_path)):
        await prepare_skill_evaluation_set(
            db=db,
            skill=skill,
            user_id=TEST_USER_ID,
            source_kind="package_import",
            allow_llm_generation=False,
        )

    # Then: no evaluation run is created or enqueued.
    run_count = await db.scalar(select(func.count()).select_from(SkillEvaluationRun))
    assert run_count == 0


async def _package_skill(
    db: AsyncSession,
    tmp_path: Path,
    *,
    eval_payload: dict[str, JsonValue] | None,
) -> Skill:
    skill_id = uuid.uuid4()
    root = tmp_path / "skills" / str(skill_id)
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\nname: imported\n"
        'description: "Use when importing evals."\n'
        "---\n\n"
        "Follow the eval instructions.\n",
        encoding="utf-8",
    )
    if eval_payload is not None:
        eval_dir = root / "evals"
        eval_dir.mkdir()
        (eval_dir / "evals.json").write_text(
            json.dumps(eval_payload, ensure_ascii=False),
            encoding="utf-8",
        )
    skill = Skill(
        id=skill_id,
        user_id=TEST_USER_ID,
        name="Imported",
        slug=f"imported-{skill_id.hex[:8]}",
        description="Use when importing evals.",
        kind="package",
        storage_path=ensure_relative(f"skills/{skill_id}"),
        content_hash="hash",
        size_bytes=1,
        version="1.0.0",
        package_metadata={"name": "imported"},
    )
    db.add(skill)
    await db.flush()
    return skill


async def _evaluation_set(
    db: AsyncSession,
    evaluation_set_id: uuid.UUID | None,
) -> SkillEvaluationSet | None:
    if evaluation_set_id is None:
        return None
    return await db.get(SkillEvaluationSet, evaluation_set_id)


async def _evaluation_set_count(db: AsyncSession, skill: Skill) -> int:
    result = await db.scalar(
        select(func.count())
        .select_from(SkillEvaluationSet)
        .where(SkillEvaluationSet.skill_id == skill.id)
    )
    return int(result or 0)


def _fake_builder_model(payload: dict[str, JsonValue]) -> ModelBuilder:
    async def build_model(_db: AsyncSession) -> SkillBuilderChatModel:
        return SkillBuilderChatModel(
            model=FakeListChatModel(responses=[json.dumps(payload)]),
            model_name="fake-smoke-model",
        )

    return build_model
