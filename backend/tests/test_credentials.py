"""Tests for credential CRUD + field_keys cache (백로그 C)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credential import Credential
from app.services import credential_service
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """Provide a valid Fernet key so credential_service.create_credential is not 503'd."""
    import app.services.encryption as enc_mod
    from app.config import settings

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "encryption_key", key, raising=False)
    original_fernet = enc_mod._fernet
    enc_mod._fernet = None
    yield
    enc_mod._fernet = original_fernet


def _make_legacy_credential(
    *,
    user_id: uuid.UUID = TEST_USER_ID,
    name: str = "Legacy Key",
    data: dict | None = None,
) -> Credential:
    """Build a Credential row with field_keys explicitly None (pre-M2 legacy shape)."""
    return Credential(
        user_id=user_id,
        name=name,
        credential_type="api_key",
        provider_name="custom",
        data_encrypted=json.dumps(data or {"api_key": "secret"}),
        field_keys=None,
    )


@pytest.mark.asyncio
async def test_create_credential_populates_field_keys(client: AsyncClient, db: AsyncSession):
    """POST /api/credentials stores field_keys cache matching the input payload keys."""
    resp = await client.post(
        "/api/credentials",
        json={
            "name": "My Key",
            "credential_type": "api_key",
            "provider_name": "custom",
            "data": {"api_key": "secret-123"},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["field_keys"] == ["api_key"]

    row = (
        await db.execute(select(Credential).where(Credential.id == uuid.UUID(body["id"])))
    ).scalar_one()
    assert row.field_keys == ["api_key"]


@pytest.mark.asyncio
async def test_update_credential_data_syncs_field_keys(client: AsyncClient, db: AsyncSession):
    """PUT with new data regenerates the field_keys cache."""
    create_resp = await client.post(
        "/api/credentials",
        json={
            "name": "Rotate Me",
            "credential_type": "oauth",
            "provider_name": "custom",
            "data": {"api_key": "old"},
        },
    )
    credential_id = create_resp.json()["id"]

    update_resp = await client.put(
        f"/api/credentials/{credential_id}",
        json={"data": {"client_id": "abc", "client_secret": "xyz"}},
    )
    assert update_resp.status_code == 200
    assert sorted(update_resp.json()["field_keys"]) == ["client_id", "client_secret"]

    row = (
        await db.execute(select(Credential).where(Credential.id == uuid.UUID(credential_id)))
    ).scalar_one()
    assert row.field_keys is not None
    assert sorted(row.field_keys) == ["client_id", "client_secret"]


@pytest.mark.asyncio
async def test_update_credential_name_only_preserves_field_keys(
    client: AsyncClient, db: AsyncSession
):
    """PUT with name-only must NOT touch field_keys cache."""
    create_resp = await client.post(
        "/api/credentials",
        json={
            "name": "Before",
            "credential_type": "api_key",
            "provider_name": "custom",
            "data": {"api_key": "stay"},
        },
    )
    credential_id = create_resp.json()["id"]

    update_resp = await client.put(
        f"/api/credentials/{credential_id}",
        json={"name": "After"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["name"] == "After"
    assert update_resp.json()["field_keys"] == ["api_key"]

    row = (
        await db.execute(select(Credential).where(Credential.id == uuid.UUID(credential_id)))
    ).scalar_one()
    assert row.field_keys == ["api_key"]


@pytest.mark.asyncio
async def test_list_credentials_avoids_decryption(client: AsyncClient, db: AsyncSession):
    """GET /api/credentials serves field_keys from the cache without any Fernet decryption."""
    # Seed a few credentials via the real API so the field_keys cache is populated.
    for i in range(3):
        resp = await client.post(
            "/api/credentials",
            json={
                "name": f"Key-{i}",
                "credential_type": "api_key",
                "provider_name": "custom",
                "data": {f"field_{i}": f"value-{i}"},
            },
        )
        assert resp.status_code == 201

    with patch(
        "app.services.credential_service.decrypt_api_key",
        side_effect=AssertionError(
            "decrypt_api_key must not be called on list when cache is populated"
        ),
    ) as spy:
        list_resp = await client.get("/api/credentials")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 3
        for item in list_resp.json():
            assert len(item["field_keys"]) == 1

    assert spy.call_count == 0


@pytest.mark.asyncio
async def test_extract_field_keys_fallback_for_legacy_row(db: AsyncSession):
    """Legacy rows with field_keys=None fall back to the decryption path."""
    legacy = _make_legacy_credential(data={"api_key": "legacy-secret"})
    db.add(legacy)
    await db.commit()
    await db.refresh(legacy)
    assert legacy.field_keys is None

    with patch(
        "app.services.credential_service.decrypt_api_key",
        return_value=json.dumps({"api_key": "legacy-secret"}),
    ) as spy:
        keys = credential_service.extract_field_keys(legacy)

    assert keys == ["api_key"]
    assert spy.call_count == 1
