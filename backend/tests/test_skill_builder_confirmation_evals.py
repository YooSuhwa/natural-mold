from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.schemas.skill_builder import JsonValue
from app.services import skill_builder_service
from app.skills import service as skill_service
from tests.conftest import TEST_USER_ID


def _skill_content(name: str = "notes") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        'description: "Use when summarizing notes into action items."\n'
        "---\n\n"
        "Use when summarizing meeting notes.\n"
    )


def _draft_with_evals() -> dict[str, JsonValue]:
    return {
        "name": "Notes",
        "slug": "notes",
        "description": "Use when summarizing notes into action items.",
        "files": [
            {"path": "SKILL.md", "content": _skill_content(), "role": "skill"},
            {
                "path": "evals/evals.json",
                "content": (
                    "{"
                    '"schema_version": 1,'
                    '"name": "Builder smoke",'
                    '"evals": ['
                    "{"
                    '"input": "Summarize the launch notes.",'
                    '"expected": {"contains": ["owner", "due date"]},'
                    '"tags": ["smoke"]'
                    "}"
                    "]"
                    "}"
                ),
                "role": "eval",
            },
        ],
        "credential_requirements": [],
        "execution_profile": {"requires_network": False},
    }


@pytest.mark.asyncio
async def test_confirm_session_creates_evaluation_set_from_draft_evals(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        session = await skill_builder_service.create_session(
            db,
            user_id=TEST_USER_ID,
            user_request="회의록 요약 스킬 만들어줘",
        )
        await skill_builder_service.save_draft_package(db, session, draft=_draft_with_evals())

        skill = await skill_builder_service.confirm_session(db, session, user_id=TEST_USER_ID)

        result = await db.execute(
            select(SkillEvaluationSet).where(SkillEvaluationSet.skill_id == skill.id)
        )
        evaluation_set = result.scalar_one()

    assert evaluation_set.user_id == TEST_USER_ID
    assert evaluation_set.source_kind == "builder"
    assert evaluation_set.name == "Builder smoke"
    assert evaluation_set.evals == [
        {
            "input": "Summarize the launch notes.",
            "expected": {"contains": ["owner", "due date"]},
            "tags": ["smoke"],
            "metadata": {},
        }
    ]


@pytest.mark.asyncio
async def test_confirm_session_copies_builder_eval_result_to_completed_run(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        session = await skill_builder_service.create_session(
            db,
            user_id=TEST_USER_ID,
            user_request="회의록 요약 스킬 만들어줘",
        )
        session.eval_result = {
            "runner_model": "gpt-5.1-mini",
            "runner_version": "deterministic-1",
            "grader_prompt_version": "grader-2026-06-13",
            "eval_schema_version": 1,
            "summary": {"pass_rate": 1, "case_count": 1},
            "benchmark": {"with_skill_pass_rate": 1, "without_skill_pass_rate": 0},
            "case_results": [{"case_index": 0, "status": "passed", "score": 1}],
            "artifact_path": "skill_builder/session-1/eval",
        }
        await skill_builder_service.save_draft_package(db, session, draft=_draft_with_evals())

        skill = await skill_builder_service.confirm_session(db, session, user_id=TEST_USER_ID)

        result = await db.execute(
            select(SkillEvaluationRun).where(SkillEvaluationRun.skill_id == skill.id)
        )
        run = result.scalar_one()

    assert run.status == "completed"
    assert run.skill_version == skill.version
    assert run.skill_content_hash == skill.content_hash
    assert run.runner_model == "gpt-5.1-mini"
    assert run.runner_version == "deterministic-1"
    assert run.grader_prompt_version == "grader-2026-06-13"
    assert run.eval_schema_version == 1
    assert run.summary == {"pass_rate": 1, "case_count": 1}
    assert run.benchmark == {"with_skill_pass_rate": 1, "without_skill_pass_rate": 0}
    assert run.case_results == [{"case_index": 0, "status": "passed", "score": 1}]
    assert run.artifact_path == "skill_builder/session-1/eval"
    assert run.started_at is not None
    assert run.completed_at is not None
