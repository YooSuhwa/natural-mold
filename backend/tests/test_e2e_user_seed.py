from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password, verify_password
from app.config import settings
from app.models.user import User
from app.seed.e2e_user import seed_e2e_user


@pytest.mark.asyncio
async def test_seed_e2e_user_creates_super_user(db: AsyncSession, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "e2e_seed_user_enabled", True)
    monkeypatch.setattr(settings, "e2e_user_email", "playwright-e2e@moldy.dev")
    monkeypatch.setattr(settings, "e2e_user_password", "correct horse battery staple 42")
    monkeypatch.setattr(settings, "e2e_user_name", "E2E User")

    user = await seed_e2e_user(db)
    await db.commit()

    assert user is not None
    assert user.email == "playwright-e2e@moldy.dev"
    assert user.name == "E2E User"
    assert user.is_active is True
    assert user.is_super_user is True
    assert verify_password("correct horse battery staple 42", user.hashed_password)


@pytest.mark.asyncio
async def test_seed_e2e_user_updates_existing_account(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "e2e_seed_user_enabled", True)
    monkeypatch.setattr(settings, "e2e_user_email", "PLAYWRIGHT-E2E@MOLDY.DEV")
    monkeypatch.setattr(settings, "e2e_user_password", "correct horse battery staple 42")
    monkeypatch.setattr(settings, "e2e_user_name", "E2E User")

    existing = User(
        email="playwright-e2e@moldy.dev",
        name="Old Name",
        hashed_password=hash_password("old-password"),
        is_active=False,
        is_super_user=False,
    )
    db.add(existing)
    await db.commit()

    user = await seed_e2e_user(db)
    await db.commit()

    assert user is not None
    assert user.id == existing.id
    assert user.name == "E2E User"
    assert user.is_active is True
    assert user.is_super_user is True
    assert verify_password("correct horse battery staple 42", user.hashed_password)

    count = await db.scalar(select(func.count()).select_from(User))
    assert count == 1


@pytest.mark.asyncio
async def test_seed_e2e_user_skips_production(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "e2e_seed_user_enabled", True)
    monkeypatch.setattr(settings, "e2e_user_email", "playwright-e2e@moldy.dev")
    monkeypatch.setattr(settings, "e2e_user_password", "correct horse battery staple 42")
    monkeypatch.setattr(settings, "e2e_user_name", "E2E User")

    user = await seed_e2e_user(db)
    await db.commit()

    assert user is None
    count = await db.scalar(select(func.count()).select_from(User))
    assert count == 0
