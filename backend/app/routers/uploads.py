"""File upload router — backs P1-7 chat attachments.

Single endpoint:
- POST /api/uploads (multipart) → ``UploadResponse`` with the public URL.
- GET  /api/uploads/{id} → serves the file inline so the frontend can render
  previews via ``<img src=…>`` / ``<a href=…>``.

Files are stored on local disk under ``settings.upload_dir`` keyed by UUID;
S3 migration would only swap the storage backend, not the API shape.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import file_not_found
from app.models.message_attachment import MessageAttachment
from app.schemas.upload import UploadResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["uploads"])

# MIME types accepted by the upload endpoint. Kept narrow on purpose for
# the PoC — text, common images, PDF. Anything outside this list is rejected
# with 422 to avoid surprising the LLM with unknown blobs.
_ALLOWED_PREFIXES = ("image/", "text/")
_ALLOWED_EXACT = {"application/pdf", "application/json"}


def _is_allowed(mime: str) -> bool:
    if any(mime.startswith(p) for p in _ALLOWED_PREFIXES):
        return True
    return mime in _ALLOWED_EXACT


def _ensure_dir() -> Path:
    base = Path(settings.upload_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _write_upload_file(storage_path: Path, contents: bytes) -> None:
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_bytes(contents)


def _safe_extension(filename: str) -> str:
    """Return a sanitized extension (with leading dot) or empty string."""

    suffix = Path(filename).suffix
    # Strip anything weird; keep ascii letters/digits up to 8 chars.
    cleaned = "".join(c for c in suffix if c.isalnum() or c == ".")
    return cleaned[:9]


@router.post("/api/uploads", response_model=UploadResponse, status_code=201)
async def create_upload(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
    _csrf: None = Depends(verify_csrf),
) -> MessageAttachment:
    mime = file.content_type or "application/octet-stream"
    if not _is_allowed(mime):
        raise HTTPException(
            status_code=422,
            detail=f"unsupported mime type: {mime}",
        )

    # Stream into memory under cap, then write atomically. UploadFile.read()
    # without size buffers the entire body — fine for the 20 MiB cap.
    contents = await file.read(settings.upload_max_bytes + 1)
    if len(contents) > settings.upload_max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds max size {settings.upload_max_bytes} bytes",
        )

    upload_id = uuid.uuid4()
    base = await asyncio.to_thread(_ensure_dir)
    ext = _safe_extension(file.filename or "")
    storage_path = base / f"{upload_id}{ext}"
    await asyncio.to_thread(_write_upload_file, storage_path, contents)

    row = MessageAttachment(
        id=upload_id,
        user_id=user.id,
        filename=file.filename or storage_path.name,
        mime_type=mime,
        size_bytes=len(contents),
        storage_path=str(storage_path),
        url=f"/api/uploads/{upload_id}",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.get("/api/uploads/{upload_id}")
async def get_upload(
    upload_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> FileResponse:
    """Serve the stored file inline — used by the frontend preview cards.

    Auth + ownership guarded (Phase 0): only the uploader may fetch. The
    browser sends the session cookie on same-origin ``<img src>`` / ``<a href>``
    requests, so previews keep working. A missing row and a row owned by
    someone else collapse to the **same 404** so the endpoint can't be used to
    probe which upload ids exist (enumeration-oracle uniformity — see
    project conventions on 404/403 parity).
    """

    result = await db.execute(
        select(MessageAttachment).where(MessageAttachment.id == upload_id)
    )
    row = result.scalar_one_or_none()
    if row is None or row.user_id != user.id:
        raise file_not_found()

    path = Path(row.storage_path)
    exists = await asyncio.to_thread(path.is_file)
    if not exists:
        raise file_not_found()
    return FileResponse(
        path,
        media_type=row.mime_type,
        filename=row.filename,
    )
