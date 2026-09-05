from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
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


async def test_llm_generator_previews_only_visible_regular_skill_files(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _package_skill(
        db,
        tmp_path,
        skill_body="Use when inspecting files.",
        storage_prefix=".hidden-parent/skills",
    )
    root = tmp_path / ".hidden-parent" / "skills" / str(skill.id)
    (root / "visible.txt").write_text("visible payload\n", encoding="utf-8")
    nested = root / "nested"
    nested.mkdir()
    (nested / "visible.txt").write_text("nested visible\n", encoding="utf-8")
    (root / ".hidden.txt").write_text("hidden root value\n", encoding="utf-8")
    hidden_dir = root / ".hidden-dir"
    hidden_dir.mkdir()
    (hidden_dir / "secret.txt").write_text("hidden directory value\n", encoding="utf-8")

    outside = tmp_path / "outside.txt"
    outside.write_text("external secret value\n", encoding="utf-8")
    (root / "outside-link.txt").symlink_to(outside)
    (root / "inside-link.txt").symlink_to(root / "visible.txt")

    seen_messages: list[list[BaseMessage]] = []

    with patch(_DATA_ROOT_PATCH, str(tmp_path)):
        await generate_skill_smoke_eval_payload(
            db,
            skill=skill,
            model_builder=_recording_model_builder(seen_messages),
        )

    prompt = json.loads(str(seen_messages[0][1].content))
    files = prompt["skill"]["files"]

    assert [item["path"] for item in files] == [
        "SKILL.md",
        "nested/visible.txt",
        "visible.txt",
    ]
    assert "external secret value" not in str(files)


async def test_llm_generator_rejects_symlinked_skill_root(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    skill = await _package_skill(db, tmp_path, skill_body="Original package content.")
    root = tmp_path / "skills" / str(skill.id)
    (root / "SKILL.md").unlink()
    root.rmdir()
    outside = tmp_path / "outside-package"
    outside.mkdir()
    (outside / "SKILL.md").write_text("external root secret\n", encoding="utf-8")
    root.symlink_to(outside, target_is_directory=True)
    seen_messages: list[list[BaseMessage]] = []

    with patch(_DATA_ROOT_PATCH, str(tmp_path)):
        await generate_skill_smoke_eval_payload(
            db,
            skill=skill,
            model_builder=_recording_model_builder(seen_messages),
        )

    prompt = json.loads(str(seen_messages[0][1].content))

    assert prompt["skill"]["files"] == []
    assert "external root secret" not in str(prompt)


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


async def _package_skill(
    db: AsyncSession,
    tmp_path: Path,
    *,
    skill_body: str,
    storage_prefix: str = "skills",
) -> Skill:
    skill_id = uuid.uuid4()
    root = tmp_path / storage_prefix / str(skill_id)
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
        storage_path=ensure_relative(f"{storage_prefix}/{skill_id}"),
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


def _recording_model_builder(seen_messages: list[list[BaseMessage]]) -> ModelBuilder:
    class RecordingModel(FakeListChatModel):
        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> ChatResult:
            seen_messages.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def build_model(_db: AsyncSession) -> SkillBuilderChatModel:
        return SkillBuilderChatModel(
            model=RecordingModel(
                responses=[
                    json.dumps(
                        {
                            "evals": [
                                {
                                    "input": "Inspect files",
                                    "expected": "Visible files are considered",
                                }
                            ]
                        }
                    )
                ]
            ),
            model_name="recording-model",
        )

    return build_model
