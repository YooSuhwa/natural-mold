"""Tests for the greenfield credential domain — CRUD, encryption round-trip,
audit logging, and per-user isolation."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import registry
from app.credentials import service as credential_service
from app.models.credential import Credential
from app.models.user import User
from app.security.key_provider import get_active_key_id
from tests.conftest import TEST_USER_ID


@pytest.fixture(autouse=True)
async def _ensure_test_user(db: AsyncSession):
    """Ensure the mock test user exists for FK constraints."""

    existing = await db.execute(select(User).where(User.id == TEST_USER_ID))
    if existing.scalar_one_or_none() is None:
        db.add(User(id=TEST_USER_ID, email="test@test.com", name="Test User"))
        await db.commit()


# -- Catalog -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_credential_types_catalog(client: AsyncClient) -> None:
    response = await client.get("/api/credential-types")
    assert response.status_code == 200
    body = response.json()
    keys = {item["key"] for item in body}
    assert {"naver_search", "openai", "anthropic", "http_bearer"} <= keys


@pytest.mark.asyncio
async def test_credential_type_detail(client: AsyncClient) -> None:
    response = await client.get("/api/credential-types/naver_search")
    assert response.status_code == 200
    body = response.json()
    field_names = [p["name"] for p in body["properties"]]
    assert field_names == ["client_id", "client_secret"]
    assert body["has_test"] is True


@pytest.mark.asyncio
async def test_credential_type_unknown(client: AsyncClient) -> None:
    response = await client.get("/api/credential-types/does-not-exist")
    assert response.status_code == 404


# -- CRUD --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_credential_round_trip(
    client: AsyncClient, db: AsyncSession
) -> None:
    response = await client.post(
        "/api/credentials",
        json={
            "definition_key": "naver_search",
            "name": "Naver Test",
            "data": {"client_id": "id-123", "client_secret": "secret-xyz"},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    cred_id = uuid.UUID(body["id"])

    assert body["definition_key"] == "naver_search"
    assert sorted(body["field_keys"]) == ["client_id", "client_secret"]
    assert body["key_id"] == get_active_key_id()
    assert body["status"] == "active"

    # Decrypt and verify the original payload survives the round-trip.
    row = (
        await db.execute(select(Credential).where(Credential.id == cred_id))
    ).scalar_one()
    decrypted = credential_service.decrypt_data(row.data_encrypted)
    assert decrypted == {"client_id": "id-123", "client_secret": "secret-xyz"}
    assert row.key_id == get_active_key_id()


@pytest.mark.asyncio
async def test_create_credential_unknown_definition(client: AsyncClient) -> None:
    response = await client.post(
        "/api/credentials",
        json={
            "definition_key": "definitely_not_a_thing",
            "name": "x",
            "data": {},
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_list_credentials_does_not_leak_data(client: AsyncClient) -> None:
    await client.post(
        "/api/credentials",
        json={
            "definition_key": "openai",
            "name": "OpenAI",
            "data": {"api_key": "sk-secret"},
        },
    )
    response = await client.get("/api/credentials")
    assert response.status_code == 200
    body = response.json()
    assert len(body) >= 1
    item = next(b for b in body if b["definition_key"] == "openai")
    assert item["field_keys"] == ["api_key"]
    # The decrypted payload must NEVER appear in a list response.
    assert "data" not in item
    assert "data_encrypted" not in item


@pytest.mark.asyncio
async def test_get_credential_omits_data(client: AsyncClient) -> None:
    create = await client.post(
        "/api/credentials",
        json={
            "definition_key": "anthropic",
            "name": "Anth",
            "data": {"api_key": "k"},
        },
    )
    cred_id = create.json()["id"]
    response = await client.get(f"/api/credentials/{cred_id}")
    assert response.status_code == 200
    body = response.json()
    assert "data" not in body
    assert body["field_keys"] == ["api_key"]


@pytest.mark.asyncio
async def test_patch_credential_updates_data_and_key_id(
    client: AsyncClient, db: AsyncSession
) -> None:
    create = await client.post(
        "/api/credentials",
        json={
            "definition_key": "openai",
            "name": "Old",
            "data": {"api_key": "old"},
        },
    )
    cred_id = create.json()["id"]

    patch = await client.patch(
        f"/api/credentials/{cred_id}",
        json={
            "name": "New",
            "data": {"api_key": "rotated", "organization": "org-1"},
        },
    )
    assert patch.status_code == 200
    body = patch.json()
    assert body["name"] == "New"
    assert sorted(body["field_keys"]) == ["api_key", "organization"]

    row = (
        await db.execute(
            select(Credential).where(Credential.id == uuid.UUID(cred_id))
        )
    ).scalar_one()
    decrypted = credential_service.decrypt_data(row.data_encrypted)
    assert decrypted == {"api_key": "rotated", "organization": "org-1"}


@pytest.mark.asyncio
async def test_patch_name_only_preserves_data(
    client: AsyncClient, db: AsyncSession
) -> None:
    create = await client.post(
        "/api/credentials",
        json={
            "definition_key": "openai",
            "name": "Before",
            "data": {"api_key": "stay"},
        },
    )
    cred_id = create.json()["id"]
    initial = (
        await db.execute(
            select(Credential).where(Credential.id == uuid.UUID(cred_id))
        )
    ).scalar_one()
    initial_blob = initial.data_encrypted

    patch = await client.patch(
        f"/api/credentials/{cred_id}", json={"name": "After"}
    )
    assert patch.status_code == 200
    assert patch.json()["name"] == "After"

    row = (
        await db.execute(
            select(Credential).where(Credential.id == uuid.UUID(cred_id))
        )
    ).scalar_one()
    assert row.data_encrypted == initial_blob


@pytest.mark.asyncio
async def test_delete_credential(
    client: AsyncClient, db: AsyncSession
) -> None:
    create = await client.post(
        "/api/credentials",
        json={
            "definition_key": "openai",
            "name": "Delete Me",
            "data": {"api_key": "k"},
        },
    )
    cred_id = uuid.UUID(create.json()["id"])

    response = await client.delete(f"/api/credentials/{cred_id}")
    assert response.status_code == 204

    row = (
        await db.execute(select(Credential).where(Credential.id == cred_id))
    ).scalar_one_or_none()
    assert row is None


# -- Audit log ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_log_records_create_and_update(
    client: AsyncClient,
) -> None:
    create = await client.post(
        "/api/credentials",
        json={
            "definition_key": "openai",
            "name": "Audit Me",
            "data": {"api_key": "x"},
        },
    )
    cred_id = create.json()["id"]
    await client.patch(
        f"/api/credentials/{cred_id}",
        json={"data": {"api_key": "y"}},
    )

    logs = await client.get(f"/api/credentials/{cred_id}/audit-logs")
    assert logs.status_code == 200
    actions = [log["action"] for log in logs.json()]
    # Newest-first ordering — update precedes create.
    assert actions[0] == "update"
    assert actions[-1] == "create"


# -- Per-user isolation ------------------------------------------------------


@pytest.mark.asyncio
async def test_other_user_cannot_access(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A credential created by another user is invisible via the API."""

    other_id = uuid.uuid4()
    db.add(User(id=other_id, email="other@test.com", name="Other"))
    await db.commit()
    await credential_service.create(
        db,
        user_id=other_id,
        definition_key="openai",
        name="Other's key",
        data={"api_key": "secret"},
    )
    await db.commit()

    listing = await client.get("/api/credentials")
    body = listing.json()
    assert all(item["user_id"] != str(other_id) for item in body)


# -- Reserved marker ---------------------------------------------------------


@pytest.mark.asyncio
async def test_reserved_marker_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/credentials",
        json={
            "definition_key": "openai",
            "name": "[m10-auto-seed] custom",
            "data": {"api_key": "x"},
        },
    )
    assert response.status_code in (400, 422)


# -- Registry sanity ---------------------------------------------------------


def test_registry_lookup() -> None:
    naver = registry.get("naver_search")
    assert naver is not None
    assert naver.test is not None
    assert naver.authenticate is not None
