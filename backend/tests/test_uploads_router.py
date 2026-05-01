"""Tests for app.routers.uploads — file upload + retrieval."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config import settings
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

    # GET serves the bytes back
    upload_id = body["id"]
    resp = await client.get(f"/api/uploads/{upload_id}")
    assert resp.status_code == 200
    assert resp.content == b"hi there"


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_mime(client: AsyncClient, tmp_path: Path):
    settings.upload_dir = str(tmp_path / "uploads")
    files = {"file": ("bad.bin", io.BytesIO(b"\x00\x01"), "application/octet-stream")}
    resp = await client.post("/api/uploads", files=files)
    assert resp.status_code == 422
