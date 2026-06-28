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

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_db, verify_csrf
from app.error_codes import file_not_found
from app.models.message_attachment import MessageAttachment
from app.schemas.artifact import ArtifactTextContent
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


def _normalize_mime(mime: str) -> str:
    """Lowercased base mime with ``; params`` stripped — for security/type
    comparisons, since the client-supplied mime varies in case and params."""

    return mime.split(";", 1)[0].strip().lower()


def _is_inline_safe(mime: str) -> bool:
    """Whether an upload may be served ``inline`` without XSS risk: only PDF and
    raster images. SVG is scriptable, so it (and everything else) is download-only."""

    base = _normalize_mime(mime)
    return base == "application/pdf" or (base.startswith("image/") and base != "image/svg+xml")


def _is_textual_mime(mime: str) -> bool:
    base = _normalize_mime(mime)
    return base.startswith("text/") or base == "application/json"


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


def _read_file_prefix(path: Path, n: int) -> bytes:
    with path.open("rb") as fh:
        return fh.read(n)


async def _get_owned_attachment(
    db: AsyncSession, upload_id: uuid.UUID, user: CurrentUser
) -> MessageAttachment:
    """Fetch an upload the caller owns, else 404.

    Missing row and foreign-owned row collapse to the same 404 so the endpoint
    can't probe which upload ids exist (enumeration-oracle uniformity).
    """

    result = await db.execute(select(MessageAttachment).where(MessageAttachment.id == upload_id))
    row = result.scalar_one_or_none()
    if row is None or row.user_id != user.id:
        raise file_not_found()
    return row


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

    row = await _get_owned_attachment(db, upload_id, user)
    path = Path(row.storage_path)
    exists = await asyncio.to_thread(path.is_file)
    if not exists:
        raise file_not_found()
    # Serve preview-safe types inline (PDF iframe, raster images) so the frontend
    # renders them in-place. Anything that the browser could execute as active
    # content in our own origin — ``text/html``, ``image/svg+xml`` — is forced to
    # ``attachment`` so navigating to ``/api/uploads/{id}`` downloads instead of
    # rendering+running it (the mime is client-supplied, so don't trust it for
    # inline). ``nosniff`` also blocks content-type sniffing into a script.
    #
    # Normalize first: the mime is client-supplied, browsers match it
    # case-insensitively and ignore ``; charset=...`` params, so an exact
    # ``!= "image/svg+xml"`` compare against the raw value would let
    # ``image/svg+XML`` / ``image/svg+xml; charset=utf-8`` slip through as inline.
    inline_ok = _is_inline_safe(row.mime_type)
    return FileResponse(
        path,
        media_type=row.mime_type,
        filename=row.filename,
        content_disposition_type="inline" if inline_ok else "attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.get("/api/uploads/{upload_id}/content", response_model=ArtifactTextContent)
async def get_upload_content(
    upload_id: uuid.UUID,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> ArtifactTextContent:
    """Text body of a text-ish upload, for in-place previews.

    The artifact preview registry's text providers (markdown/json/csv/code/text)
    fetch body text by id from the artifact content endpoint — which 404s for an
    upload id. This is the upload-scoped equivalent so attachment previews aren't
    empty. Non-text uploads (images/pdf preview from the URL directly) 404 here.
    """

    row = await _get_owned_attachment(db, upload_id, user)
    if not _is_textual_mime(row.mime_type):
        raise file_not_found()
    path = Path(row.storage_path)
    if not await asyncio.to_thread(path.is_file):
        raise file_not_found()

    max_bytes = settings.artifact_preview_max_text_bytes
    data = await asyncio.to_thread(_read_file_prefix, path, max_bytes + 1)
    # Match get_upload — never let the JSON body be sniffed into something active.
    response.headers["X-Content-Type-Options"] = "nosniff"
    return ArtifactTextContent(
        text=data[:max_bytes].decode("utf-8", errors="replace"),
        truncated=len(data) > max_bytes,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
    )
