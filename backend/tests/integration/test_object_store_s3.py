"""The object storage contract on a real S3-compatible server (plan §11.6, D19).

Lane only (``-m integration``) with ``STORAGE_TEST_S3_ENDPOINT`` (and key, secret) set,
e.g. the bundled SeaweedFS: ``docker compose --profile s3 up s3``.
"""

from __future__ import annotations

import asyncio
import os
import urllib.request
import uuid
from pathlib import Path

import pytest

from app.config import settings
from app.storage.object_store import S3ObjectStore, _s3_client
from tests.test_object_store import store_contract

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("STORAGE_TEST_S3_ENDPOINT"), reason="needs an S3-compatible server"
    ),
]


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> S3ObjectStore:
    monkeypatch.setattr(settings, "storage_s3_endpoint_url", os.environ["STORAGE_TEST_S3_ENDPOINT"])
    monkeypatch.setattr(settings, "storage_s3_access_key_id", os.environ["STORAGE_TEST_S3_KEY"])
    monkeypatch.setattr(
        settings, "storage_s3_secret_access_key", os.environ["STORAGE_TEST_S3_SECRET"]
    )
    monkeypatch.setattr(settings, "storage_s3_create_bucket", True)
    return S3ObjectStore(_s3_client(), bucket=f"moldy-test-{uuid.uuid4().hex[:8]}")


async def test_the_s3_store_meets_the_contract(s3: S3ObjectStore, tmp_path: Path) -> None:
    await s3.ensure_bucket()

    await store_contract(s3, tmp_path)


async def test_a_signed_url_reads_the_object_for_five_minutes(s3: S3ObjectStore) -> None:
    await s3.ensure_bucket()
    await s3.put_bytes("tenants/t1/a.txt", b"signed")

    url = await s3.signed_url("tenants/t1/a.txt")

    assert url is not None and "X-Amz-Expires=300" in url

    def fetch() -> bytes:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - test server
            return response.read()

    assert await asyncio.to_thread(fetch) == b"signed"


async def test_wrong_keys_are_reported_without_the_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "storage_s3_endpoint_url", os.environ["STORAGE_TEST_S3_ENDPOINT"])
    monkeypatch.setattr(settings, "storage_s3_access_key_id", "wrong")
    monkeypatch.setattr(settings, "storage_s3_secret_access_key", "not-the-secret-value")
    monkeypatch.setattr(settings, "storage_s3_create_bucket", True)
    store = S3ObjectStore(_s3_client(), bucket="moldy-wrong")

    health = await store.health()

    assert not health.ok and "not-the-secret-value" not in health.detail
