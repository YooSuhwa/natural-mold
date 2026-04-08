"""Tests for app.routers.builder — Builder v2 API endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.builder_session import BuilderSession
from app.models.model import Model
from app.models.user import User
from app.schemas.builder import BuilderStatus
from tests.conftest import TEST_USER_ID


async def _seed(db: AsyncSession) -> None:
    """Create User + Model for confirm tests."""
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
# POST /api/builder — start_build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_build(client: AsyncClient, db: AsyncSession):
    await _seed(db)

    resp = await client.post("/api/builder", json={"user_request": "날씨 봇 만들어줘"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "building"
    assert body["user_request"] == "날씨 봇 만들어줘"
    assert body["id"] is not None


@pytest.mark.asyncio
async def test_start_build_empty_request(client: AsyncClient):
    resp = await client.post("/api/builder", json={"user_request": ""})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/builder/{id} — get_build_session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session(client: AsyncClient, db: AsyncSession):
    await _seed(db)

    create_resp = await client.post(
        "/api/builder", json={"user_request": "검색 에이전트"}
    )
    session_id = create_resp.json()["id"]

    resp = await client.get(f"/api/builder/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["user_request"] == "검색 에이전트"


@pytest.mark.asyncio
async def test_get_session_not_found(client: AsyncClient):
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/api/builder/{fake_id}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SESSION_NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /api/builder/{id}/confirm — confirm_build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_build(client: AsyncClient, db: AsyncSession):
    """Confirm a PREVIEW session creates an agent and returns 201."""
    await _seed(db)

    # Create a BuilderSession directly in PREVIEW state with draft_config
    session = BuilderSession(
        user_id=TEST_USER_ID,
        user_request="날씨 봇",
        status=BuilderStatus.PREVIEW,
        draft_config={
            "name": "Weather Bot",
            "name_ko": "날씨 봇",
            "description": "날씨를 알려주는 봇",
            "system_prompt": "You are a weather bot.",
            "tools": [],
            "middlewares": [],
            "model_name": "GPT-4o",
        },
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    resp = await client.post(f"/api/builder/{session.id}/confirm")
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "날씨 봇"
    assert body["system_prompt"] == "You are a weather bot."


@pytest.mark.asyncio
async def test_confirm_not_preview(client: AsyncClient, db: AsyncSession):
    """Confirming a BUILDING session returns 422."""
    await _seed(db)

    # Create a session still in BUILDING state
    resp = await client.post("/api/builder", json={"user_request": "테스트"})
    session_id = resp.json()["id"]

    resp = await client.post(f"/api/builder/{session_id}/confirm")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "SESSION_NOT_PREVIEW"


@pytest.mark.asyncio
async def test_confirm_not_found(client: AsyncClient):
    """Confirming a non-existent session returns 404."""
    fake_id = str(uuid.uuid4())
    resp = await client.post(f"/api/builder/{fake_id}/confirm")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_confirm_completed_without_agent(client: AsyncClient, db: AsyncSession):
    """Confirming a COMPLETED session without agent_id falls through to PREVIEW check."""
    await _seed(db)

    session = BuilderSession(
        user_id=TEST_USER_ID,
        user_request="날씨 봇",
        status=BuilderStatus.COMPLETED,
        agent_id=None,
        draft_config={"name": "Weather Bot"},
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    # COMPLETED but no agent_id → falls through to status check → not PREVIEW → 422
    resp = await client.post(f"/api/builder/{session.id}/confirm")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_confirm_already_confirming(client: AsyncClient, db: AsyncSession):
    """Confirming a session that is already CONFIRMING returns 409."""
    await _seed(db)

    session = BuilderSession(
        user_id=TEST_USER_ID,
        user_request="test",
        status=BuilderStatus.CONFIRMING,
        draft_config={"name": "Bot"},
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    resp = await client.post(f"/api/builder/{session.id}/confirm")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SESSION_CONFIRMING"


@pytest.mark.asyncio
async def test_confirm_no_draft_config(client: AsyncClient, db: AsyncSession):
    """Confirming a PREVIEW session without draft_config returns 422."""
    await _seed(db)

    session = BuilderSession(
        user_id=TEST_USER_ID,
        user_request="test",
        status=BuilderStatus.PREVIEW,
        draft_config=None,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    resp = await client.post(f"/api/builder/{session.id}/confirm")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "NO_DRAFT_CONFIG"


@pytest.mark.asyncio
async def test_confirm_no_model_returns_422(client: AsyncClient, db: AsyncSession):
    """Confirming when no models available returns 422."""
    # Seed only user, no model
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    await db.commit()

    session = BuilderSession(
        user_id=TEST_USER_ID,
        user_request="test",
        status=BuilderStatus.PREVIEW,
        draft_config={
            "name": "Bot",
            "name_ko": "봇",
            "description": "d",
            "system_prompt": "p",
            "tools": [],
            "middlewares": [],
            "model_name": "nonexistent",
        },
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    resp = await client.post(f"/api/builder/{session.id}/confirm")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "MODEL_NOT_FOUND"


# ---------------------------------------------------------------------------
# GET /api/builder/{id}/stream — SSE streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_not_found(client: AsyncClient):
    """Streaming a non-existent session returns 404."""
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/api/builder/{fake_id}/stream")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_stream_not_building_state(client: AsyncClient, db: AsyncSession):
    """Streaming a session not in BUILDING state returns 409."""
    await _seed(db)

    session = BuilderSession(
        user_id=TEST_USER_ID,
        user_request="test",
        status=BuilderStatus.PREVIEW,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    resp = await client.get(f"/api/builder/{session.id}/stream")
    assert resp.status_code == 409
