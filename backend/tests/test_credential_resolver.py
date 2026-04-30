"""Tests for ``app.services.credential_resolver``.

Covers the tiered credential lookup that powers ``/models/{id}/test``,
``/health/check``, and (via the matching TS helper) the frontend Health
panel and Test dialog. The intent is to keep "which credential should this
model use" answers identical across every surface.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential import Credential
from app.models.model import Model
from app.models.user import User
from app.services.credential_resolver import (
    pick_default_for_provider,
    resolve_credential_for_model,
)
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession):
    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await db.commit()


async def _make_credential(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    name: str,
    definition_key: str = "openai",
) -> Credential:
    cred = await credential_service.create(
        db,
        user_id=user_id,
        definition_key=definition_key,
        name=name,
        data={"api_key": f"sk-{name}"},
    )
    return cred


def _make_model(*, default_credential_id: uuid.UUID | None = None) -> Model:
    return Model(
        id=uuid.uuid4(),
        provider="openai",
        model_name="gpt-5.5",
        display_name="GPT-5.5",
        default_credential_id=default_credential_id,
    )


@pytest.mark.asyncio
async def test_resolver_explicit_credential_wins(db: AsyncSession) -> None:
    """When the caller passes a credential_id, use it (not the default)."""

    explicit = await _make_credential(db, TEST_USER_ID, name="explicit")
    default_cred = await _make_credential(db, TEST_USER_ID, name="default")
    model = _make_model(default_credential_id=default_cred.id)

    result = await resolve_credential_for_model(db, model, explicit.id, TEST_USER_ID)
    assert result is not None
    assert result.id == explicit.id


@pytest.mark.asyncio
async def test_resolver_falls_back_to_model_default(db: AsyncSession) -> None:
    """No explicit → use ``model.default_credential_id``."""

    default_cred = await _make_credential(db, TEST_USER_ID, name="default")
    model = _make_model(default_credential_id=default_cred.id)

    result = await resolve_credential_for_model(db, model, None, TEST_USER_ID)
    assert result is not None
    assert result.id == default_cred.id


@pytest.mark.asyncio
async def test_resolver_returns_none_when_neither(db: AsyncSession) -> None:
    """No explicit, no default → None (caller decides what to do)."""

    model = _make_model()
    result = await resolve_credential_for_model(db, model, None, TEST_USER_ID)
    assert result is None


@pytest.mark.asyncio
async def test_resolver_rejects_other_users_explicit(db: AsyncSession) -> None:
    """Explicit credential owned by someone else returns None."""

    other_user_id = uuid.uuid4()
    db.add(User(id=other_user_id, email="other@test.com", name="Other"))
    await db.commit()

    other_cred = await _make_credential(db, other_user_id, name="other")
    default_cred = await _make_credential(db, TEST_USER_ID, name="my-default")
    model = _make_model(default_credential_id=default_cred.id)

    result = await resolve_credential_for_model(
        db, model, other_cred.id, TEST_USER_ID
    )
    # Explicit-but-invalid does NOT silently fall through to default — the
    # mismatch usually indicates stale UI state and we want it surfaced.
    assert result is None


@pytest.mark.asyncio
async def test_resolver_skips_default_owned_by_someone_else(db: AsyncSession) -> None:
    """Stale ``default_credential_id`` pointing at another user's row → None."""

    other_user_id = uuid.uuid4()
    db.add(User(id=other_user_id, email="other2@test.com", name="Other2"))
    await db.commit()

    other_cred = await _make_credential(db, other_user_id, name="other-default")
    model = _make_model(default_credential_id=other_cred.id)

    result = await resolve_credential_for_model(db, model, None, TEST_USER_ID)
    assert result is None


def test_pick_default_for_provider_exact_match() -> None:
    creds = [
        Credential(
            id=uuid.uuid4(),
            user_id=TEST_USER_ID,
            name="anthropic-key",
            definition_key="anthropic",
            data_encrypted="x",
            field_keys=[],
            is_shared=False,
            status="active",
        ),
        Credential(
            id=uuid.uuid4(),
            user_id=TEST_USER_ID,
            name="openai-key",
            definition_key="openai",
            data_encrypted="x",
            field_keys=[],
            is_shared=False,
            status="active",
        ),
    ]
    chosen = pick_default_for_provider(creds, "openai")
    assert chosen is not None
    assert chosen.definition_key == "openai"


def test_pick_default_for_provider_falls_back_to_first() -> None:
    creds = [
        Credential(
            id=uuid.uuid4(),
            user_id=TEST_USER_ID,
            name="anthropic-key",
            definition_key="anthropic",
            data_encrypted="x",
            field_keys=[],
            is_shared=False,
            status="active",
        ),
    ]
    chosen = pick_default_for_provider(creds, "openai")
    assert chosen is not None
    assert chosen.definition_key == "anthropic"


def test_pick_default_for_provider_empty_list() -> None:
    assert pick_default_for_provider([], "openai") is None
