"""Backwards-compatible shim for the assistant/builder catalog.

The greenfield Tool/Credential domain owns CRUD via :mod:`app.routers.tools`
and :mod:`app.tools.runner`. The Builder/Assistant sub-agents only need a
read-only catalog (name + description) so they can suggest tools to the user;
this thin module supplies that view without re-importing any of the legacy
PREBUILT/CUSTOM scaffolding deleted in M5.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool import Tool


async def get_tools_catalog(db: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Return the user's enabled tools + every system-owned tool (``user_id IS NULL``)."""

    result = await db.execute(
        select(Tool.id, Tool.name, Tool.description, Tool.definition_key).where(
            or_(Tool.user_id == user_id, Tool.user_id.is_(None))
        )
    )
    return [
        {
            "id": str(row.id),
            "name": row.name,
            "description": row.description or "",
            "definition_key": row.definition_key,
        }
        for row in result.all()
    ]


__all__ = ["get_tools_catalog"]
