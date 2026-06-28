"""Tests for app.routers.uploads — file upload + retrieval."""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.message_attachment import MessageAttachment
from app.models.user import User
from tests.conftest import TEST_USER_ID  # noqa: F401  — import side effects


@pytest.mark.asyncio
async def test_upload_text_file_roundtrip(client: AsyncClient, tmp_path: Path):
    settings.upload_dir = str(tmp_path / "uploads")

    files = {"file": ("hello.txt", io.BytesIO(b"hi there"), "text/plain")}
    resp = await client.post("/api/uploads", files=files)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["filename"] == "hello.txt"
    assert body["mime_type"] == "text/plain"
    assert body["size_bytes"] == 8
    assert body["url"].startswith("/api/uploads/")

    # GET serves the bytes back, inline so previews (img/iframe) render in-place.
    upload_id = body["id"]
    resp = await client.get(f"/api/uploads/{upload_id}")
    assert resp.status_code == 200
    assert resp.content == b"hi there"
    assert "inline" in resp.headers.get("content-disposition", "")


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_mime(client: AsyncClient, tmp_path: Path):
    settings.upload_dir = str(tmp_path / "uploads")
    files = {"file": ("bad.bin", io.BytesIO(b"\x00\x01"), "application/octet-stream")}
    resp = await client.post("/api/uploads", files=files)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_upload_other_user_is_404(client: AsyncClient, db: AsyncSession, tmp_path: Path):
    """Phase 0: a row owned by someone else is indistinguishable from missing.

    The file exists on disk, so a 200 would leak it if the ownership guard were
    absent — we assert 404 (parity with a missing row, no enumeration oracle).
    """

    other_id = uuid.UUID("00000000-0000-0000-0000-0000000000ff")
    db.add(User(id=other_id, email="other@test.com", name="Other"))
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_id = uuid.uuid4()
    storage_path = upload_dir / f"{upload_id}.txt"
    storage_path.write_bytes(b"secret bytes")
    db.add(
        MessageAttachment(
            id=upload_id,
            user_id=other_id,
            filename="secret.txt",
            mime_type="text/plain",
            size_bytes=12,
            storage_path=str(storage_path),
            url=f"/api/uploads/{upload_id}",
        )
    )
    await db.commit()

    # ``client`` is authenticated as TEST_USER_ID — not the owner.
    resp = await client.get(f"/api/uploads/{upload_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_upload_requires_auth(raw_client: AsyncClient):
    """Phase 0: the real JWT path rejects an unauthenticated fetch with 401."""

    resp = await raw_client.get(f"/api/uploads/{uuid.uuid4()}")
    assert resp.status_code == 401
