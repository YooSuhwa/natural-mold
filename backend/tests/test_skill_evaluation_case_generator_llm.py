from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.agent import SkillBuilderChatModel
from app.models.skill import Skill
from app.schemas.skill_builder import JsonValue
from app.services.skill_evaluation_case_generator_llm import (
    ModelBuilder,
    SkillEvaluationCaseGenerationError,
    generate_skill_smoke_eval_payload,
)
from app.services.system_credential_resolver import SystemModelNotConfiguredError
from app.storage.paths import ensure_relative
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio
_DATA_ROOT_PATCH = "app.storage.paths.settings.data_root"


async def test_llm_generator_returns_normalized_eval_payload(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a package skill and an LLM that returns Moldy eval JSON.
    skill = await _package_skill(db, tmp_path, skill_body="Use when summarizing notes.")
    model = _fake_builder_model(
        {
            "name": "Generated smoke evaluation",
            "description": "Generated from the skill.",
            "evals": [
                {
                    "input": "Summarize these meeting notes.",
                    "expected": "A concise summary.",
                    "tags": ["smoke"],
                    "metadata": {"expectations": ["Includes decisions"]},
                }
            ],
        }
    )

    # When: smoke evals are generated.
    with patch(_DATA_ROOT_PATCH, str(tmp_path)):
        generated = await generate_skill_smoke_eval_payload(db, skill=skill, model_builder=model)

    # Then: the output is normalized and marked as generated.
    assert generated.model_name == "fake-smoke-model"
    assert generated.payload["name"] == "Generated smoke evaluation"
    assert generated.payload["evals"][0]["input"] == "Summarize these meeting notes."
    assert generated.payload["evals"][0]["metadata"]["generated"] is True
    assert generated.payload["evals"][0]["metadata"]["source_schema"] == "moldy"


async def test_llm_generator_rejects_invalid_model_json(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a package skill and an LLM that returns invalid JSON.
    skill = await _package_skill(db, tmp_path, skill_body="Use when extracting data.")
    model = _fake_builder_model_text("not json")

    # When/Then: generation fails with a typed error.
    with (
        patch(_DATA_ROOT_PATCH, str(tmp_path)),
        pytest.raises(SkillEvaluationCaseGenerationError, match="invalid JSON"),
    ):
        await generate_skill_smoke_eval_payload(db, skill=skill, model_builder=model)


async def test_llm_generator_caps_case_count(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a model that returns more cases than the generator limit.
    skill = await _package_skill(db, tmp_path, skill_body="Use when drafting emails.")
    model = _fake_builder_model(
        {"evals": [{"input": f"Task {index}", "expected": "Result"} for index in range(8)]}
    )

    # When: smoke evals are generated.
    with patch(_DATA_ROOT_PATCH, str(tmp_path)):
        generated = await generate_skill_smoke_eval_payload(db, skill=skill, model_builder=model)

    # Then: the returned payload is capped to five cases.
    assert len(generated.payload["evals"]) == 5


async def test_llm_generator_does_not_run_when_system_model_missing(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    # Given: a model builder that raises the system LLM configuration error.
    skill = await _package_skill(db, tmp_path, skill_body="Use when researching.")

    async def missing_model(_db: AsyncSession) -> SkillBuilderChatModel:
        raise SystemModelNotConfiguredError("text_primary")

    # When/Then: the configuration error propagates for the preparation service to handle.
    with (
        patch(_DATA_ROOT_PATCH, str(tmp_path)),
        pytest.raises(SystemModelNotConfiguredError),
    ):
        await generate_skill_smoke_eval_payload(db, skill=skill, model_builder=missing_model)


async def _package_skill(db: AsyncSession, tmp_path: Path, *, skill_body: str) -> Skill:
    skill_id = uuid.uuid4()
    root = tmp_path / "skills" / str(skill_id)
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        f'---\nname: generated\ndescription: "Use when generating evals."\n---\n\n{skill_body}\n',
        encoding="utf-8",
    )
    skill = Skill(
        id=skill_id,
        user_id=TEST_USER_ID,
        name="Generated",
        slug=f"generated-{skill_id.hex[:8]}",
        description="Use when generating evals.",
        kind="package",
        storage_path=ensure_relative(f"skills/{skill_id}"),
        content_hash="hash",
        size_bytes=1,
        version="1.0.0",
        package_metadata={"name": "generated"},
    )
    db.add(skill)
    await db.flush()
    return skill


def _fake_builder_model(payload: dict[str, JsonValue]) -> ModelBuilder:
    return _fake_builder_model_text(json.dumps(payload))


def _fake_builder_model_text(response: str) -> ModelBuilder:
    async def build_model(_db: AsyncSession) -> SkillBuilderChatModel:
        return SkillBuilderChatModel(
            model=FakeListChatModel(responses=[response]),
            model_name="fake-smoke-model",
        )

    return build_model
