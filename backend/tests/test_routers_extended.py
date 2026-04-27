"""Extended router tests — models, builder confirm paths."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.models.model import Model
from app.models.user import User
from app.schemas.builder import BuilderStatus
from app.services.builder_service import create_session
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_user_model() -> tuple[uuid.UUID, uuid.UUID]:
    """Create user + model, return (model_id, user_id)."""
    async with TestSession() as db:
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
        return model.id, user.id


# ---------------------------------------------------------------------------
# PUT /api/models/{model_id} — update success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_model_via_api(client: AsyncClient):
    """PUT /api/models/{id} — updates model fields."""
    resp = await client.post(
        "/api/models",
        json={"provider": "openai", "model_name": "gpt-4o", "display_name": "GPT-4o"},
    )
    assert resp.status_code == 201
    model_id = resp.json()["id"]

    resp = await client.put(
        f"/api/models/{model_id}",
        json={"display_name": "GPT-4o Updated"},
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "GPT-4o Updated"


# ---------------------------------------------------------------------------
# PUT /api/models/{model_id} — not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_model_not_found(client: AsyncClient):
    """PUT /api/models/{id} — 404 for nonexistent model."""
    fake_id = "00000000-0000-0000-0000-000000000099"
    resp = await client.put(
        f"/api/models/{fake_id}",
        json={"display_name": "Ghost"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/models/bulk — provider not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_create_models_provider_not_found(client: AsyncClient):
    """POST /api/models/bulk — 404 when provider doesn't exist."""
    resp = await client.post(
        "/api/models/bulk",
        json={
            "provider_id": "00000000-0000-0000-0000-000000000099",
            "models": [{"model_name": "test", "display_name": "Test"}],
        },
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Builder confirm — already completed (idempotent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builder_confirm_session_not_found(client: AsyncClient):
    """POST /api/builder/{id}/confirm — 404 for nonexistent session."""
    fake_id = "00000000-0000-0000-0000-000000000099"
    resp = await client.post(f"/api/builder/{fake_id}/confirm")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Builder confirm — CONFIRMING → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builder_confirm_already_confirming(client: AsyncClient):
    """POST /api/builder/{id}/confirm — CONFIRMING returns 409."""
    await _seed_user_model()

    async with TestSession() as db:
        session = await create_session(db, TEST_USER_ID, "test")
        session.status = BuilderStatus.CONFIRMING
        await db.commit()
        session_id = session.id

    resp = await client.post(f"/api/builder/{session_id}/confirm")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Builder confirm — BUILDING → validation error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builder_confirm_not_preview(client: AsyncClient):
    """POST /api/builder/{id}/confirm — non-PREVIEW returns 422."""
    await _seed_user_model()

    async with TestSession() as db:
        session = await create_session(db, TEST_USER_ID, "test")
        # status is BUILDING (default)
        await db.commit()
        session_id = session.id

    resp = await client.post(f"/api/builder/{session_id}/confirm")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Builder confirm — no draft_config → validation error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builder_confirm_no_draft_config(client: AsyncClient):
    """POST /api/builder/{id}/confirm — PREVIEW without draft_config returns 422."""
    await _seed_user_model()

    async with TestSession() as db:
        session = await create_session(db, TEST_USER_ID, "test")
        session.status = BuilderStatus.PREVIEW
        session.draft_config = None
        await db.commit()
        session_id = session.id

    resp = await client.post(f"/api/builder/{session_id}/confirm")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Builder confirm — ValueError (no models) → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_builder_confirm_no_models(client: AsyncClient):
    """POST /api/builder/{id}/confirm — ValueError from confirm_build → 422."""
    # Don't seed model so confirm_build will raise ValueError
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        await db.commit()

    async with TestSession() as db:
        session = await create_session(db, TEST_USER_ID, "test")
        session.status = BuilderStatus.PREVIEW
        session.draft_config = {
            "name": "Bot",
            "name_ko": "봇",
            "description": "d",
            "system_prompt": "p",
            "tools": [],
            "middlewares": [],
            "model_name": "nonexistent",
        }
        await db.commit()
        session_id = session.id

    resp = await client.post(f"/api/builder/{session_id}/confirm")
    assert resp.status_code == 422
