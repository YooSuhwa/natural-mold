from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.agent import SkillBuilderChatModel
from app.services.skill_evaluation_case_generator_llm import GeneratedSkillEvaluationPayload
from app.skills import service as skill_service
from tests.test_marketplace_install_evaluation_preparation import (
    _ensure_test_user,
    _latest_evaluation_set,
    _published_skill_item,
)

pytestmark = pytest.mark.asyncio


async def test_marketplace_install_commits_before_auto_prepare(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _ensure_test_user(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        item = await _published_skill_item(db, tmp_path, include_evals=False)
        await db.commit()

        prepare = AsyncMock(return_value=None)

        async def assert_no_install_transaction(*args, **kwargs) -> None:
            prepare_db = args[0]
            assert isinstance(prepare_db, AsyncSession)
            assert not prepare_db.in_transaction()
            return await prepare(*args, **kwargs)

        with patch(
            "app.routers.marketplace.evaluation_preparation.prepare_installed_skill_evaluation_set",
            side_effect=assert_no_install_transaction,
        ):
            response = await client.post(
                f"/api/marketplace/items/{item.id}/install",
                json={"install_mode": "reuse_or_update"},
            )

    assert response.status_code == 201, response.text
    prepare.assert_awaited_once()


async def test_marketplace_install_generates_evals_without_open_transaction(
    client: AsyncClient,
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    await _ensure_test_user(db)
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        item = await _published_skill_item(db, tmp_path, include_evals=False)
        await db.commit()

        async def build_model(_db: AsyncSession) -> SkillBuilderChatModel:
            return SkillBuilderChatModel(
                model=FakeListChatModel(responses=[]),
                model_name="fake-smoke-model",
            )

        async def generate_payload(
            generation_db: AsyncSession,
            **_kwargs,
        ) -> GeneratedSkillEvaluationPayload:
            assert not generation_db.in_transaction()
            return GeneratedSkillEvaluationPayload(
                payload={
                    "name": "Generated smoke evaluation",
                    "evals": [{"input": "Summarize this note.", "expected": "Summary."}],
                },
                model_name="fake-smoke-model",
            )

        with (
            patch(
                "app.marketplace.evaluation_preparation.build_skill_builder_chat_model",
                side_effect=build_model,
            ),
            patch(
                "app.services.skill_evaluation_set_preparation.generate_skill_smoke_eval_payload",
                side_effect=generate_payload,
            ),
        ):
            response = await client.post(
                f"/api/marketplace/items/{item.id}/install",
                json={"install_mode": "reuse_or_update"},
            )

    assert response.status_code == 201, response.text
    installed_skill_id = uuid.UUID(response.json()["installed_skill_id"])
    assert await _latest_evaluation_set(db, installed_skill_id) is not None
