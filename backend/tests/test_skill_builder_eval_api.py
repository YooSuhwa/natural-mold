from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.skill_builder_session import SkillBuilderSession
from app.models.system_llm_setting import SystemLlmSetting
from app.schemas.skill_builder import JsonValue
from app.services import skill_builder_eval_service, skill_builder_service
from tests.conftest import TEST_USER_ID

pytestmark = pytest.mark.asyncio


async def _configure_system_llm(db: AsyncSession) -> None:
    credential = await credential_service.create(
        db,
        user_id=None,
        definition_key="openai",
        name="builder-eval-key",
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
        "description": "Use when extracting action items from meeting notes.",
        "files": [
            {
                "path": "SKILL.md",
                "content": (
                    "---\n"
                    "name: notes\n"
                    'description: "Use when extracting action items."\n'
                    "---\n\n"
                    "Extract action items, owners, and deadlines."
                ),
                "role": "skill",
            }
        ],
        "credential_requirements": [],
        "execution_profile": {"requires_network": False},
    }


async def _session_with_draft(db: AsyncSession) -> SkillBuilderSession:
    session = await skill_builder_service.create_session(
        db,
        user_id=TEST_USER_ID,
        user_request="회의록에서 액션 아이템을 표로 추출하는 스킬을 평가해줘",
    )
    await skill_builder_service.save_draft_package(db, session, draft=_draft())
    await db.commit()
    await db.refresh(session)
    return session


async def test_run_builder_eval_persists_session_eval_result(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _configure_system_llm(db)
    session = await _session_with_draft(db)

    with patch.object(skill_builder_eval_service.settings, "data_root", str(tmp_path)):
        response = await client.post(f"/api/skill-builder/{session.id}/evals/run")

    assert response.status_code == 200, response.text
    body = response.json()
    eval_result = body["eval_result"]
    assert eval_result["template_key"] == "structured_extraction"
    assert eval_result["summary"]["case_count"] == 3
    assert eval_result["benchmark"]["with_skill_min_score"] == 1
    assert eval_result["benchmark"]["without_skill_max_score"] == 0
    assert "expectations" in eval_result
    assert "execution_metrics" in eval_result
    assert "timing" in eval_result
    assert "claims" in eval_result
    assert "eval_feedback" in eval_result
    assert body["draft_package"]["benchmark"] == eval_result["benchmark"]
    assert (tmp_path / "skill-builder-evals" / str(session.id) / "with-skill").is_dir()
    assert (tmp_path / "skill-builder-evals" / str(session.id) / "without-skill").is_dir()


async def test_run_builder_eval_requires_system_llm_before_artifacts(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    session = await _session_with_draft(db)

    with patch.object(skill_builder_eval_service.settings, "data_root", str(tmp_path)):
        response = await client.post(f"/api/skill-builder/{session.id}/evals/run")
    await db.refresh(session)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SYSTEM_LLM_NOT_CONFIGURED"
    assert session.eval_result is None
    assert not (tmp_path / "skill-builder-evals").exists()
