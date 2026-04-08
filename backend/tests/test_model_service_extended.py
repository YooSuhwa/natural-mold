"""Extended tests for app.services.model_service — update, delete, bulk, edge cases."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_provider import LLMProvider
from app.models.user import User
from app.services.model_service import (
    bulk_create_models,
    create_model,
    delete_model,
    get_model,
    update_model,
)
from tests.conftest import TEST_USER_ID


async def _seed(db: AsyncSession) -> tuple[User, LLMProvider]:
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    provider = LLMProvider(name="TestProvider", provider_type="openai")
    db.add(provider)
    await db.flush()
    return user, provider


# ---------------------------------------------------------------------------
# update_model — basic fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_model_basic(db: AsyncSession):
    """update_model changes fields and returns updated model dict."""
    from app.schemas.model import ModelCreate, ModelUpdate

    await _seed(db)
    await db.commit()

    created = await create_model(
        db,
        ModelCreate(
            provider="openai",
            model_name="gpt-4o",
            display_name="GPT-4o",
        ),
    )
    model_id = created["id"]

    result = await update_model(
        db,
        model_id,
        ModelUpdate(display_name="GPT-4o Updated"),
    )
    assert result is not None
    assert result["display_name"] == "GPT-4o Updated"


# ---------------------------------------------------------------------------
# update_model — not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_model_not_found(db: AsyncSession):
    """update_model returns None for nonexistent model."""
    from app.schemas.model import ModelUpdate

    result = await update_model(db, uuid.uuid4(), ModelUpdate(display_name="X"))
    assert result is None


# ---------------------------------------------------------------------------
# update_model — is_default resets others
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_model_is_default_resets_others(db: AsyncSession):
    """Setting is_default=True on one model clears it from others."""
    from app.schemas.model import ModelCreate, ModelUpdate

    await _seed(db)
    await db.commit()

    m1 = await create_model(
        db,
        ModelCreate(provider="openai", model_name="gpt-4o", display_name="GPT-4o", is_default=True),
    )
    m2 = await create_model(
        db,
        ModelCreate(
            provider="anthropic",
            model_name="claude-sonnet-4-20250514",
            display_name="Claude Sonnet 4",
            is_default=False,
        ),
    )

    # Set m2 as default
    result = await update_model(db, m2["id"], ModelUpdate(is_default=True))
    assert result is not None
    assert result["is_default"] is True

    # m1 should no longer be default
    m1_reloaded = await get_model(db, m1["id"])
    assert m1_reloaded is not None
    assert m1_reloaded.is_default is False


# ---------------------------------------------------------------------------
# update_model — with api_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_model_with_api_key(db: AsyncSession):
    """update_model encrypts api_key."""
    from app.schemas.model import ModelCreate, ModelUpdate

    await _seed(db)
    await db.commit()

    created = await create_model(
        db,
        ModelCreate(provider="openai", model_name="gpt-4o", display_name="GPT-4o"),
    )
    model_id = created["id"]

    result = await update_model(
        db,
        model_id,
        ModelUpdate(api_key="sk-test-key-123"),
    )
    assert result is not None

    # Verify the encrypted key is stored
    model = await get_model(db, model_id)
    assert model is not None
    assert model.api_key_encrypted is not None


# ---------------------------------------------------------------------------
# update_model — with provider_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_model_with_provider_id(db: AsyncSession):
    """update_model with provider_id links to provider."""
    from app.schemas.model import ModelCreate, ModelUpdate

    _, provider = await _seed(db)
    await db.commit()

    created = await create_model(
        db,
        ModelCreate(
            provider="openai",
            model_name="gpt-4o",
            display_name="GPT-4o",
            provider_id=provider.id,
        ),
    )

    result = await update_model(
        db,
        created["id"],
        ModelUpdate(display_name="GPT-4o V2"),
    )
    assert result is not None
    assert str(result["provider_id"]) == str(provider.id)


# ---------------------------------------------------------------------------
# create_model — provider_type mismatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_model_provider_type_mismatch(db: AsyncSession):
    """create_model raises ValueError when provider type doesn't match."""
    from app.schemas.model import ModelCreate

    _, provider = await _seed(db)
    await db.commit()

    with pytest.raises(ValueError, match="provider_type mismatch"):
        await create_model(
            db,
            ModelCreate(
                provider="anthropic",  # mismatches provider's "openai" type
                model_name="claude",
                display_name="Claude",
                provider_id=provider.id,
            ),
        )


# ---------------------------------------------------------------------------
# delete_model — exists
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_model_success(db: AsyncSession):
    """delete_model returns True and removes model."""
    from app.schemas.model import ModelCreate

    await _seed(db)
    await db.commit()

    created = await create_model(
        db,
        ModelCreate(provider="openai", model_name="gpt-4o", display_name="GPT-4o"),
    )
    result = await delete_model(db, created["id"])
    assert result is True

    model = await get_model(db, created["id"])
    assert model is None


# ---------------------------------------------------------------------------
# delete_model — not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_model_not_found(db: AsyncSession):
    """delete_model returns False for nonexistent model."""
    result = await delete_model(db, uuid.uuid4())
    assert result is False


# ---------------------------------------------------------------------------
# bulk_create — provider not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_create_models_provider_not_found(db: AsyncSession):
    """bulk_create_models returns None when provider doesn't exist."""
    from app.schemas.model import ModelBulkCreate, ModelBulkItem

    result = await bulk_create_models(
        db,
        ModelBulkCreate(
            provider_id=uuid.uuid4(),
            models=[ModelBulkItem(model_name="test", display_name="Test")],
        ),
    )
    assert result is None
