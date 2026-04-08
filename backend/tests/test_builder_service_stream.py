"""Tests for builder_service.run_build_stream, _save_phase_result, and streaming paths."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.models.user import User
from app.schemas.builder import BuilderStatus
from app.services.builder_service import (
    _save_phase_result,
    create_session,
    get_session,
    run_build_stream,
)
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_for_stream(db: AsyncSession) -> None:
    """Seed user and model for stream tests."""
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    model = Model(
        provider="openai",
        model_name="gpt-4o",
        display_name="GPT-4o",
        is_default=True,
    )
    db.add(model)
    await db.commit()


# ---------------------------------------------------------------------------
# run_build_stream — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_build_stream_success(db: AsyncSession):
    """run_build_stream yields SSE events and sets session to PREVIEW on success."""
    await _seed_for_stream(db)
    session = await create_session(db, TEST_USER_ID, "날씨 봇 만들어줘")
    session.status = BuilderStatus.STREAMING
    await db.commit()

    session_id = session.id
    user_id = session.user_id

    # Mock run_builder_pipeline to yield one phase-completed event + final state
    async def mock_pipeline(**kwargs):
        yield {
            "events": [
                {"event_type": "phase_progress", "phase": 1, "status": "completed"},
            ],
            "state_update": {"current_phase": 1, "intent": "weather bot"},
        }
        yield {
            "events": [],
            "state_update": {
                "current_phase": 6,
                "draft_config": {"name": "날씨 봇"},
            },
        }

    with (
        patch(
            "app.services.builder_service.run_builder_pipeline",
            side_effect=mock_pipeline,
        ),
        patch(
            "app.services.builder_service.get_tools_catalog",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.services.builder_service._get_middlewares_catalog",
            return_value=[],
        ),
        patch(
            "app.services.builder_service.async_session_factory",
            return_value=TestSession(),
        ),
    ):
        collected = []
        async for chunk in run_build_stream(session_id, user_id, "날씨 봇 만들어줘"):
            collected.append(chunk)

    # Should have phase_progress event, build_preview, and stream_end
    all_text = "".join(collected)
    assert "phase_progress" in all_text
    assert "build_preview" in all_text
    assert "stream_end" in all_text


# ---------------------------------------------------------------------------
# run_build_stream — pipeline error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_build_stream_error(db: AsyncSession):
    """Pipeline exception yields error SSE + sets status FAILED."""
    await _seed_for_stream(db)
    session = await create_session(db, TEST_USER_ID, "에러 테스트")
    session.status = BuilderStatus.STREAMING
    await db.commit()

    session_id = session.id
    user_id = session.user_id

    async def mock_pipeline_fail(**kwargs):
        raise RuntimeError("Something went wrong")

    with (
        patch(
            "app.services.builder_service.run_builder_pipeline",
            side_effect=mock_pipeline_fail,
        ),
        patch(
            "app.services.builder_service.get_tools_catalog",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.services.builder_service._get_middlewares_catalog",
            return_value=[],
        ),
        patch(
            "app.services.builder_service.async_session_factory",
            return_value=TestSession(),
        ),
    ):
        collected = []
        async for chunk in run_build_stream(session_id, user_id, "에러 테스트"):
            collected.append(chunk)

    all_text = "".join(collected)
    assert "error" in all_text
    assert "stream_end" in all_text


# ---------------------------------------------------------------------------
# run_build_stream — finally rollback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_build_stream_session_not_found(db: AsyncSession):
    """run_build_stream handles missing session in final update gracefully."""
    await _seed_for_stream(db)

    # Use a fake session_id that doesn't exist
    fake_session_id = uuid.uuid4()
    user_id = TEST_USER_ID

    async def mock_pipeline(**kwargs):
        yield {
            "events": [],
            "state_update": {"current_phase": 6, "draft_config": {"name": "Bot"}},
        }

    with (
        patch(
            "app.services.builder_service.run_builder_pipeline",
            side_effect=mock_pipeline,
        ),
        patch(
            "app.services.builder_service.get_tools_catalog",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.services.builder_service._get_middlewares_catalog",
            return_value=[],
        ),
        patch(
            "app.services.builder_service.async_session_factory",
            return_value=TestSession(),
        ),
    ):
        collected = []
        async for chunk in run_build_stream(fake_session_id, user_id, "test"):
            collected.append(chunk)

    all_text = "".join(collected)
    assert "error" in all_text
    assert "stream_end" in all_text


# ---------------------------------------------------------------------------
# _save_phase_result — success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_phase_result(db: AsyncSession):
    """_save_phase_result persists phase data to the session."""
    await _seed_for_stream(db)
    session = await create_session(db, TEST_USER_ID, "저장 테스트")
    await db.commit()

    session_id = session.id

    with patch(
        "app.services.builder_service.async_session_factory",
        return_value=TestSession(),
    ):
        await _save_phase_result(
            session_id,
            {
                "current_phase": 3,
                "project_path": "/tmp/test",
                "intent": "Test intent",
                "tools": ["web_search"],
                "system_prompt": "Be helpful.",
            },
        )

    # Verify saved data (use a fresh session to avoid stale cache)
    async with TestSession() as fresh_db:
        reloaded = await get_session(fresh_db, session_id, TEST_USER_ID)
        assert reloaded is not None
        assert reloaded.current_phase == 3
        assert reloaded.project_path == "/tmp/test"
        assert reloaded.intent == "Test intent"
        assert reloaded.tools_result == ["web_search"]
        assert reloaded.system_prompt == "Be helpful."


# ---------------------------------------------------------------------------
# _save_phase_result — session not found (no crash)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_phase_result_session_not_found():
    """_save_phase_result handles missing session gracefully."""
    fake_id = uuid.uuid4()

    with patch(
        "app.services.builder_service.async_session_factory",
        return_value=TestSession(),
    ):
        # Should not raise
        await _save_phase_result(fake_id, {"current_phase": 1})


# ---------------------------------------------------------------------------
# _save_phase_result — exception handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_phase_result_exception():
    """_save_phase_result logs warning on exception, doesn't crash."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(side_effect=RuntimeError("db error"))
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch(
        "app.services.builder_service.async_session_factory",
        return_value=mock_session,
    ):
        # Should not raise
        await _save_phase_result(uuid.uuid4(), {"current_phase": 1})
