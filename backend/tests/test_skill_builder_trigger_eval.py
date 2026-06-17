from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.trigger_eval import (
    MAX_DESCRIPTION_LENGTH,
    deterministic_split,
    generate_trigger_examples,
    rewrite_description,
    select_best_candidate,
)
from app.credentials import service as credential_service
from app.models.system_llm_setting import SystemLlmSetting
from app.schemas.skill_builder import JsonValue
from app.services import skill_builder_service
from tests.conftest import TEST_USER_ID


async def _configure_system_llm(db: AsyncSession) -> None:
    credential = await credential_service.create(
        db,
        user_id=None,
        definition_key="openai",
        name="trigger-eval-key",
        data={"api_key": "sk-test"},
        is_system=True,
    )
    db.add(
        SystemLlmSetting(
            role="text_primary",
            credential_id=credential.id,
            model_name="gpt-5.4",
        )
    )
    await db.commit()


def _draft() -> dict[str, JsonValue]:
    return {
        "name": "Notes",
        "slug": "notes",
        "description": "Helper",
        "files": [
            {
                "path": "SKILL.md",
                "content": '---\nname: notes\ndescription: "Helper"\n---\n\nExtract actions.',
                "role": "skill",
            }
        ],
        "credential_requirements": [],
        "execution_profile": {"requires_network": False},
    }


def test_train_test_split_preserves_positive_and_negative_examples() -> None:
    examples = generate_trigger_examples(
        name="Notes",
        description="Use when extracting action items.",
        intent="meeting notes action extraction",
    )

    split = deterministic_split(examples, seed=42)

    assert any(example.should_trigger for example in split.test)
    assert any(not example.should_trigger for example in split.test)
    assert split.train


def test_rewritten_description_stays_under_1024_chars() -> None:
    description = rewrite_description(
        name="Long",
        description="extract " * 300,
        intent="meeting action item extraction " * 100,
    )

    assert len(description) <= MAX_DESCRIPTION_LENGTH


def test_best_description_is_selected_by_test_score_before_train_score() -> None:
    selected = select_best_candidate(
        [
            {"label": "before", "description": "A", "train_score": 1, "test_score": 0.5},
            {"label": "after", "description": "B", "train_score": 0.5, "test_score": 1},
        ]
    )

    assert selected["label"] == "after"


@pytest.mark.asyncio
async def test_trigger_eval_endpoint_persists_result_and_updates_draft_description(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    await _configure_system_llm(db)
    session = await skill_builder_service.create_session(
        db,
        user_id=TEST_USER_ID,
        user_request="회의록에서 액션 아이템과 담당자를 표로 추출",
    )
    await skill_builder_service.save_draft_package(db, session, draft=_draft())
    await db.commit()

    response = await client.post(f"/api/skill-builder/{session.id}/trigger-eval/run")

    assert response.status_code == 200, response.text
    body = response.json()
    selected = body["trigger_eval_result"]["selected"]
    description = selected["description"]
    skill_file = body["draft_package"]["files"][0]["content"]
    assert body["draft_package"]["description"] == description
    assert len(description) <= MAX_DESCRIPTION_LENGTH
    assert description in skill_file
    assert body["trigger_eval_result"]["runs_per_query"] == 3
