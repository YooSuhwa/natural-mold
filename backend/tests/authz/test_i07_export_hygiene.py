"""I-07 export hygiene: redaction rules, interrupted jobs and expired archives."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

from app.config import settings
from app.models.organization import Tenant
from app.models.tenant_export import TenantExport
from app.services.platform.tenant_export_gc import run_export_gc
from app.services.platform.tenant_export_redaction import clean_json, clean_url, secret_key
from tests.conftest import TestSession

R = "<redacted>"


@pytest.mark.parametrize(
    ("key", "secret"),
    [
        ("api_key", True),
        ("apiKey", True),
        ("X-Api-Key", True),
        ("Authorization", True),
        ("client_secret", True),
        ("refresh_token", True),
        ("password", True),
        ("max_output_tokens", False),
        ("token_count", False),
        ("credential_id", False),
        ("session_id", False),
        ("possession", False),
        ("my_secretary", False),
    ],
)
def test_secret_keys_match_whole_words(key: str, secret: bool) -> None:
    assert secret_key(key) is secret


def test_urls_lose_userinfo_and_query_and_configs_lose_secrets() -> None:
    assert clean_url("https://bob:pw@api.example.com/v1?key=abc#x") == (
        "https://api.example.com/v1#x"
    )
    assert clean_url("plain text?a=1") == "plain text?a=1"
    config = {
        "apiKey": "sk-1",
        "limits": {"token_count": 3},
        "headers": [{"name": "Authorization", "value": "Bearer t"}, {"name": "X", "value": "y"}],
        "url": "https://u:p@h.example/x?token=1",
    }
    assert clean_json(config) == {
        "apiKey": R,
        "limits": {"token_count": 3},
        "headers": [{"name": "Authorization", "value": R}, {"name": "X", "value": "y"}],
        "url": "https://h.example/x",
    }


async def test_gc_fails_interrupted_jobs_and_deletes_expired_archives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    now = datetime.now(UTC)
    archive = tmp_path / "tenant_exports" / "old.zip"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"PK")
    async with TestSession() as db:
        tenant = Tenant(name="GC", slug="gc-co", secrets_namespace="gc-co")
        db.add(tenant)
        await db.flush()
        stuck = TenantExport(tenant_id=tenant.id, status="running", progress=10)
        old = TenantExport(
            tenant_id=tenant.id,
            status="succeeded",
            progress=100,
            storage_path="tenant_exports/old.zip",
            expires_at=now - timedelta(1),
        )
        fresh = TenantExport(tenant_id=tenant.id, status="failed", progress=0)
        db.add_all([stuck, old, fresh])
        await db.commit()
        await db.execute(
            update(TenantExport)
            .where(TenantExport.id == stuck.id)
            .values(updated_at=now - timedelta(hours=3))
        )
        await db.commit()
        ids = (stuck.id, old.id)

    assert await run_export_gc(maker=TestSession, now=now) == (1, 1)

    async with TestSession() as db:
        stuck_row = await db.get(TenantExport, ids[0])
        old_row = await db.get(TenantExport, ids[1])
        assert stuck_row is not None and stuck_row.status == "failed"
        assert stuck_row.error_code == "export_interrupted"
        assert old_row is not None and old_row.storage_path is None
    assert not archive.exists()
    assert await run_export_gc(maker=TestSession, now=now) == (0, 0)
