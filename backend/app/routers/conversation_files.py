from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import conversation_not_found
from app.schemas.conversation import FileItem
from app.services import chat_service
from app.services.conversation_file_service import resolve_conversation_file

router = APIRouter(tags=["conversations"])


@router.get("/api/conversations/{conversation_id}/files", response_model=list[FileItem])
async def list_conversation_files(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> list[FileItem]:
    """Unified conversation file list (D12): generated artifacts + sent
    attachments, normalized and created_at-sorted (newest first).

    Distinct path from ``/files/{file_path:path}`` below (which serves one
    generated file by path) — FastAPI never conflates ``/files`` with
    ``/files/<segment>``. Ownership-guarded like every conversation read.
    """

    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise conversation_not_found()
    return await chat_service.list_conversation_files(
        db, user_id=user.id, conversation_id=conversation_id
    )


@router.get("/api/conversations/{conversation_id}/files/{file_path:path}")
async def get_conversation_file(
    conversation_id: uuid.UUID,
    file_path: str,
    variant: Literal["original", "preview"] = Query("original"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conv = await chat_service.get_owned_conversation(db, conversation_id, user.id)
    if not conv:
        raise conversation_not_found()

    resolved = await resolve_conversation_file(
        Path(settings.conversation_output_dir),
        conversation_id,
        file_path,
        variant,
    )
    return FileResponse(
        resolved.path,
        media_type=resolved.media_type,
        headers={"Cache-Control": resolved.cache_control},
    )
