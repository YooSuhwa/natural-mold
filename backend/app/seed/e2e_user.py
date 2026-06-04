"""Seed a local-only Playwright E2E account."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password, verify_password
from app.config import settings
from app.models.user import User
from app.services import user_service

logger = logging.getLogger(__name__)


async def seed_e2e_user(db: AsyncSession) -> User | None:
    """Create or refresh the configured local E2E super user."""

    if settings.app_env == "production":
        return None
    if not settings.e2e_seed_user_enabled:
        return None

    email = settings.e2e_user_email.strip().lower()
    password = settings.e2e_user_password
    name = settings.e2e_user_name.strip() or "E2E User"
    if not email or not password:
        logger.warning("skip seed_e2e_user: E2E user email or password is empty")
        return None

    existing = await user_service.get_by_email(db, email)
    if existing is None:
        user = await user_service.create_user(
            db,
            email=email,
            password_hash=hash_password(password),
            name=name,
            display_name=name,
            is_super_user=True,
        )
        logger.info("seed_e2e_user: created %s", email)
        return user

    changed = False
    if existing.name != name:
        existing.name = name
        changed = True
    if existing.display_name is None:
        existing.display_name = name
        changed = True
    if not existing.is_active:
        existing.is_active = True
        changed = True
    if not existing.is_super_user:
        existing.is_super_user = True
        changed = True
    if not verify_password(password, existing.hashed_password):
        existing.hashed_password = hash_password(password)
        changed = True

    if changed:
        await db.flush()
        logger.info("seed_e2e_user: refreshed %s", email)
    return existing


__all__ = ["seed_e2e_user"]
