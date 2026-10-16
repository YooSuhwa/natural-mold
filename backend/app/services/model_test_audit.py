"""Credential audit row for a model test / test-preview probe."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service


async def record_model_test(
    db: AsyncSession,
    request: Request,
    *,
    credential_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    payload: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    error = payload.get("error")
    await credential_service.write_audit_log(
        db,
        credential_id=credential_id,
        actor_user_id=actor_user_id,
        action="test",
        source="api",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        metadata={**metadata, "success": payload["success"]},
        error=None if payload["success"] else (error["message"] if error else None),
    )
