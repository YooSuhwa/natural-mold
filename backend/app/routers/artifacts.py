from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, get_current_user, get_db, owned_conversation, verify_csrf
from app.error_codes import file_not_found
from app.schemas.artifact import (
    ArtifactFavoriteUpdate,
    ArtifactLibraryPage,
    ArtifactLibraryStats,
    ArtifactSummary,
)
from app.services import artifact_service
from app.services.artifact_service import ArtifactNotFoundError, is_text_preview_artifact

router = APIRouter(tags=["artifacts"])


@router.get(
    "/api/conversations/{conversation_id}/artifacts",
    response_model=list[ArtifactSummary],
    dependencies=[Depends(owned_conversation)],
)
async def list_conversation_artifacts(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await artifact_service.list_conversation_artifacts(
        db,
        user_id=user.id,
        conversation_id=conversation_id,
    )


@router.get(
    "/api/conversations/{conversation_id}/artifacts/{artifact_id}",
    response_model=ArtifactSummary,
    dependencies=[Depends(owned_conversation)],
)
async def get_conversation_artifact(
    conversation_id: uuid.UUID,
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        return await artifact_service.get_conversation_artifact_summary(
            db,
            user_id=user.id,
            conversation_id=conversation_id,
            artifact_id=artifact_id,
        )
    except ArtifactNotFoundError as exc:
        raise file_not_found() from exc


@router.get(
    "/api/conversations/{conversation_id}/artifacts/{artifact_id}/content",
    dependencies=[Depends(owned_conversation)],
)
async def get_conversation_artifact_content(
    conversation_id: uuid.UUID,
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        summary = await artifact_service.get_conversation_artifact_summary(
            db,
            user_id=user.id,
            conversation_id=conversation_id,
            artifact_id=artifact_id,
        )
        if is_text_preview_artifact(summary):
            return await artifact_service.read_artifact_text_content(
                db,
                user_id=user.id,
                artifact_id=artifact_id,
                conversation_id=conversation_id,
            )
        _artifact, _version, path = await artifact_service.get_artifact_download_path(
            db,
            user_id=user.id,
            artifact_id=artifact_id,
            conversation_id=conversation_id,
        )
        return FileResponse(
            path,
            media_type=summary.mime_type,
            headers={"Cache-Control": "private, max-age=300"},
        )
    except ArtifactNotFoundError as exc:
        raise file_not_found() from exc


@router.get(
    "/api/conversations/{conversation_id}/artifacts/{artifact_id}/download",
    dependencies=[Depends(owned_conversation)],
)
async def download_conversation_artifact(
    conversation_id: uuid.UUID,
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        artifact, _version, path = await artifact_service.get_artifact_download_path(
            db,
            user_id=user.id,
            artifact_id=artifact_id,
            conversation_id=conversation_id,
        )
        await artifact_service.record_artifact_download(
            db,
            user_id=user.id,
            artifact_id=artifact_id,
        )
        await db.commit()
        return FileResponse(
            path,
            filename=artifact.display_name,
            media_type=artifact.mime_type,
            headers={"Cache-Control": "private, max-age=300"},
        )
    except ArtifactNotFoundError as exc:
        raise file_not_found() from exc


@router.delete(
    "/api/conversations/{conversation_id}/artifacts/{artifact_id}",
    status_code=204,
    dependencies=[Depends(owned_conversation)],
)
async def delete_conversation_artifact(
    conversation_id: uuid.UUID,
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    try:
        await artifact_service.delete_artifact(
            db,
            user_id=user.id,
            artifact_id=artifact_id,
            conversation_id=conversation_id,
        )
        await db.commit()
    except ArtifactNotFoundError as exc:
        raise file_not_found() from exc


@router.get("/api/artifacts", response_model=ArtifactLibraryPage)
async def list_artifact_library(
    q: str | None = Query(None),
    agent_id: uuid.UUID | None = Query(None),
    conversation_id: uuid.UUID | None = Query(None),
    kind: str | None = Query(None),
    favorite: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await artifact_service.list_library_artifacts(
        db,
        user_id=user.id,
        q=q,
        agent_id=agent_id,
        conversation_id=conversation_id,
        kind=kind,
        favorite=favorite,
        limit=limit,
        cursor=cursor,
    )


@router.get("/api/artifacts/stats", response_model=ArtifactLibraryStats)
async def get_artifact_library_stats(
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await artifact_service.get_library_stats(db, user_id=user.id)


@router.get("/api/artifacts/recent", response_model=list[ArtifactSummary])
async def list_recent_artifacts(
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await artifact_service.list_recent_artifacts(db, user_id=user.id, limit=limit)


@router.patch("/api/artifacts/{artifact_id}", response_model=ArtifactSummary)
async def update_artifact(
    artifact_id: uuid.UUID,
    data: ArtifactFavoriteUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    try:
        summary = await artifact_service.set_artifact_favorite(
            db,
            user_id=user.id,
            artifact_id=artifact_id,
            is_favorite=data.is_favorite,
        )
        await db.commit()
        return summary
    except ArtifactNotFoundError as exc:
        raise file_not_found() from exc


@router.post("/api/artifacts/{artifact_id}/opened", response_model=ArtifactSummary)
async def record_artifact_opened(
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
):
    try:
        summary = await artifact_service.record_artifact_opened(
            db,
            user_id=user.id,
            artifact_id=artifact_id,
        )
        await db.commit()
        return summary
    except ArtifactNotFoundError as exc:
        raise file_not_found() from exc


@router.get("/api/artifacts/{artifact_id}/content")
async def get_artifact_library_content(
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        summary = await artifact_service.get_artifact_summary(
            db,
            user_id=user.id,
            artifact_id=artifact_id,
        )
        if is_text_preview_artifact(summary):
            return await artifact_service.read_artifact_text_content(
                db,
                user_id=user.id,
                artifact_id=artifact_id,
            )
        _artifact, _version, path = await artifact_service.get_artifact_download_path(
            db,
            user_id=user.id,
            artifact_id=artifact_id,
        )
        return FileResponse(
            path,
            media_type=summary.mime_type,
            headers={"Cache-Control": "private, max-age=300"},
        )
    except ArtifactNotFoundError as exc:
        raise file_not_found() from exc


@router.get("/api/artifacts/{artifact_id}/download")
async def download_artifact_library_item(
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    try:
        artifact, _version, path = await artifact_service.get_artifact_download_path(
            db,
            user_id=user.id,
            artifact_id=artifact_id,
        )
        await artifact_service.record_artifact_download(
            db,
            user_id=user.id,
            artifact_id=artifact_id,
        )
        await db.commit()
        return FileResponse(
            path,
            filename=artifact.display_name,
            media_type=artifact.mime_type,
            headers={"Cache-Control": "private, max-age=300"},
        )
    except ArtifactNotFoundError as exc:
        raise file_not_found() from exc
