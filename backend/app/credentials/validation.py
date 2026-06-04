from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.models.credential import Credential


async def require_user_credential(
    db: AsyncSession,
    *,
    credential_id: uuid.UUID | None,
    user_id: uuid.UUID,
) -> Credential | None:
    """Return a user-owned, non-system credential or raise a neutral 404."""

    if credential_id is None:
        return None
    credential = await credential_service.get_for_user(db, credential_id, user_id)
    if credential is None:
        raise HTTPException(status_code=404, detail="credential not found")
    return credential


__all__ = ["require_user_credential"]
