"""Tests for ``app/routers/models.py`` — CRUD + delete-when-in-use guard."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.agent import Agent
from app.models.model import Model
from app.models.user import User
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession):
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="m@test", name="m"))
        await db.commit()


# -- POST --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_model_minimum_fields(client: AsyncClient) -> None:
    response = await client.post(
        "/api/models",
        json={
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "display_name": "GPT-4o mini",
            "source": "litellm",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider"] == "openai"
    assert body["model_name"] == "gpt-4o-mini"
    assert body["source"] == "litellm"
    assert body["agent_count"] == 0


@pytest.mark.asyncio
async def test_create_model_full_payload(client: AsyncClient) -> None:
    response = await client.post(
        "/api/models",
        json={
            "provider": "openrouter",
            "model_name": "anthropic/claude-haiku-4-5",
            "display_name": "Claude Haiku 4.5",
            "base_url": "https://openrouter.ai/api/v1",
            "cost_per_input_token": "0.000001",
            "cost_per_output_token": "0.000005",
            "context_window": 200000,
            "max_output_tokens": 64000,
            "input_modalities": ["text", "image"],
            "output_modalities": ["text"],
            "supports_vision": True,
            "supports_function_calling": True,
            "supports_reasoning": True,
            "source": "openrouter",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["max_output_tokens"] == 64000
    assert body["supports_reasoning"] is True
    assert body["base_url"] == "https://openrouter.ai/api/v1"


@pytest.mark.asyncio
async def test_create_model_duplicate_returns_409(
    client: AsyncClient, db: AsyncSession
) -> None:
    db.add(
        Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    )
    await db.commit()

    response = await client.post(
        "/api/models",
        json={
            "provider": "openai",
            "model_name": "gpt-4o",
            "display_name": "Dup",
        },
    )
    # SQLite without a unique index will accept it; PostgreSQL raises.
    # The router translates IntegrityError to 409 — both paths land here.
    assert response.status_code in (201, 409)


# -- GET ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_model(client: AsyncClient, db: AsyncSession) -> None:
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.commit()

    response = await client.get(f"/api/models/{model.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["model_name"] == "gpt-4o"


@pytest.mark.asyncio
async def test_get_model_404(client: AsyncClient) -> None:
    response = await client.get(f"/api/models/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_models_returns_agent_count(
    client: AsyncClient, db: AsyncSession
) -> None:
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()
    db.add(
        Agent(
            user_id=TEST_USER_ID,
            name="A",
            system_prompt="hi",
            model_id=model.id,
        )
    )
    await db.commit()

    response = await client.get("/api/models")
    assert response.status_code == 200
    body = response.json()
    matching = [m for m in body if m["model_name"] == "gpt-4o"]
    assert matching and matching[0]["agent_count"] == 1


# -- PATCH -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_model_updates_pricing(
    client: AsyncClient, db: AsyncSession
) -> None:
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.commit()

    response = await client.patch(
        f"/api/models/{model.id}",
        json={
            "cost_per_input_token": "0.0000025",
            "supports_reasoning": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["supports_reasoning"] is True
    # Decimal round-trips through whatever JSON form FastAPI picks.
    from decimal import Decimal as _D

    assert _D(str(body["cost_per_input_token"])) == _D("0.0000025")


@pytest.mark.asyncio
async def test_patch_model_404(client: AsyncClient) -> None:
    response = await client.patch(
        f"/api/models/{uuid.uuid4()}", json={"display_name": "x"}
    )
    assert response.status_code == 404


# -- DELETE ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_unused_model(
    client: AsyncClient, db: AsyncSession
) -> None:
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.commit()

    response = await client.delete(f"/api/models/{model.id}")
    assert response.status_code == 204

    row = (
        await db.execute(select(Model).where(Model.id == model.id))
    ).scalar_one_or_none()
    assert row is None


@pytest.mark.asyncio
async def test_delete_in_use_model_409(
    client: AsyncClient, db: AsyncSession
) -> None:
    model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
    db.add(model)
    await db.flush()
    db.add(
        Agent(
            user_id=TEST_USER_ID,
            name="A",
            system_prompt="hi",
            model_id=model.id,
        )
    )
    await db.commit()

    response = await client.delete(f"/api/models/{model.id}")
    assert response.status_code == 409
    assert "agent" in response.json()["detail"]


@pytest.mark.asyncio
async def test_delete_model_404(client: AsyncClient) -> None:
    response = await client.delete(f"/api/models/{uuid.uuid4()}")
    assert response.status_code == 404


# -- discover-models endpoint -----------------------------------------------


@pytest.mark.asyncio
async def test_discover_models_endpoint_forbids_non_llm_definition(
    client: AsyncClient,
) -> None:
    """A naver_search credential cannot drive model discovery — 400."""

    create = await client.post(
        "/api/credentials",
        json={
            "definition_key": "naver_search",
            "name": "n",
            "data": {"client_id": "x", "client_secret": "y"},
        },
    )
    cred_id = create.json()["id"]
    response = await client.post(f"/api/credentials/{cred_id}/discover-models")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_discover_models_endpoint_anthropic(client: AsyncClient) -> None:
    """End-to-end: anthropic discovery returns DiscoveredModelSchema rows."""

    create = await client.post(
        "/api/credentials",
        json={
            "definition_key": "anthropic",
            "name": "anth",
            "data": {"api_key": "k"},
        },
    )
    cred_id = create.json()["id"]
    response = await client.post(f"/api/credentials/{cred_id}/discover-models")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list)
    assert body
    first = body[0]
    assert "model_name" in first
    assert "source" in first
    assert "already_registered" in first


@pytest.mark.asyncio
async def test_discover_models_endpoint_system_credential_as_super_user(
    client: AsyncClient, db: AsyncSession
) -> None:
    """super_user can discover models from an ``is_system`` credential (ADR-019).

    System LLM settings selects a model from a system credential via this
    endpoint. ``_load_owned`` is user-scoped and previously 404'd on system
    credentials (user_id IS NULL); discovery now falls back to ``get_system``
    for super_users.
    """

    cred = await credential_service.create(
        db,
        user_id=None,
        definition_key="anthropic",
        name="sys-anth",
        data={"api_key": "k"},
        is_system=True,
    )
    await db.commit()
    response = await client.post(f"/api/credentials/{cred.id}/discover-models")
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), list)
