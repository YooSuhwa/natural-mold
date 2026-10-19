"""Non-committing user memory settings read (parent Task 10 prepared runs).

``memory_service.get_user_settings`` inserts and *commits* a default row when
none exists. Inside the prepared-run resolver that commit would release the
canonical gateway locks mid-preparation (``prepared_run_unsupported:
inputs_unlocked``). Prepared (caller-owned, locked) sessions therefore read the
row or a transient default carrying the column defaults, never writing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import UserMemorySettings


def default_user_settings(user_id: uuid.UUID) -> UserMemorySettings:
    """Transient (never added to a session) settings with the column defaults."""

    values = {
        column.key: column.default.arg
        for column in UserMemorySettings.__table__.columns
        if column.default is not None and not callable(column.default.arg)
    }
    now = datetime.now(UTC).replace(tzinfo=None)
    return UserMemorySettings(user_id=user_id, created_at=now, updated_at=now, **values)


async def peek_user_settings(db: AsyncSession, user_id: uuid.UUID) -> UserMemorySettings:
    """Read-or-default without inserting or committing."""

    result = await db.execute(
        select(UserMemorySettings).where(UserMemorySettings.user_id == user_id)
    )
    return result.scalar_one_or_none() or default_user_settings(user_id)
