"""Tests for app.services.model_service — resolve_model + get_tools_catalog."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.model import Model
from app.models.tool import Tool
from app.models.user import User
from app.services.model_service import resolve_model
from app.services.tool_service import get_tools_catalog
from tests.conftest import TEST_USER_ID


async def _seed_user(db: AsyncSession) -> User:
    user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
    db.add(user)
    await db.flush()
    return user


async def _seed_models(db: AsyncSession) -> tuple[Model, Model]:
    """Create two models: GPT-4o (default) and Claude 3."""
    m1 = Model(
        provider="openai",
        model_name="gpt-4o",
        display_name="GPT-4o",
        is_default=True,
    )
    m2 = Model(
        provider="anthropic",
        model_name="claude-3-opus",
        display_name="Claude 3 Opus",
        is_default=False,
    )
    db.add_all([m1, m2])
    await db.flush()
    return m1, m2


# ---------------------------------------------------------------------------
# resolve_model — by display_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_by_display_name(db: AsyncSession):
    m1, _ = await _seed_models(db)
    await db.commit()

    result = await resolve_model(db, "GPT-4o")
    assert result is not None
    assert result.id == m1.id
    assert result.display_name == "GPT-4o"


# ---------------------------------------------------------------------------
# resolve_model — by provider:model_name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_by_provider_model(db: AsyncSession):
    _, m2 = await _seed_models(db)
    await db.commit()

    result = await resolve_model(db, "anthropic:claude-3-opus")
    assert result is not None
    assert result.id == m2.id
    assert result.model_name == "claude-3-opus"


# ---------------------------------------------------------------------------
# resolve_model — strict=True, not found -> None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_strict_not_found(db: AsyncSession):
    await _seed_models(db)
    await db.commit()

    result = await resolve_model(db, "nonexistent-model", strict=True)
    assert result is None


# ---------------------------------------------------------------------------
# resolve_model — strict=False, not found -> fallback to default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_fallback_default(db: AsyncSession):
    m1, _ = await _seed_models(db)
    await db.commit()

    result = await resolve_model(db, "nonexistent-model", strict=False)
    assert result is not None
    assert result.id == m1.id  # GPT-4o is default


@pytest.mark.asyncio
async def test_resolve_fallback_no_default(db: AsyncSession):
    """When strict=False and no default model exists, return None."""
    m = Model(
        provider="openai",
        model_name="gpt-4o-mini",
        display_name="GPT-4o Mini",
        is_default=False,
    )
    db.add(m)
    await db.commit()

    result = await resolve_model(db, "nonexistent", strict=False)
    assert result is None


# ---------------------------------------------------------------------------
# get_tools_catalog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tools_catalog(db: AsyncSession):
    await _seed_user(db)

    # System tool
    sys_tool = Tool(
        name="Web Search",
        type="prebuilt",
        is_system=True,
        description="Search the web",
    )
    db.add(sys_tool)

    # User tool
    user_tool = Tool(
        name="My Custom Tool",
        type="custom",
        is_system=False,
        user_id=TEST_USER_ID,
        description="A custom tool",
    )
    db.add(user_tool)

    # Another user's tool (should NOT appear)
    other_tool = Tool(
        name="Other Tool",
        type="custom",
        is_system=False,
        user_id=uuid.uuid4(),
        description="Someone else's tool",
    )
    db.add(other_tool)
    await db.commit()

    catalog = await get_tools_catalog(db, TEST_USER_ID)
    names = [item["name"] for item in catalog]

    assert "Web Search" in names  # system tool
    assert "My Custom Tool" in names  # user's own tool
    assert "Other Tool" not in names  # other user's tool

    # Check structure
    for item in catalog:
        assert "name" in item
        assert "description" in item
        assert "type" in item
