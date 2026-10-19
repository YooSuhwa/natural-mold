"""B-16 — company-scoped credentials use format 0x02 behind ``TENANT_DATA_KEYS_ENABLED``."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.platform_scope import PlatformScope
from app.config import settings
from app.credentials import rotation
from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.models.credential import Credential
from app.models.organization import Tenant
from app.models.user import User
from app.scheduler import rotate_credentials_to_active_key
from app.security import cipher, key_provider, tenant_keys
from app.security.cipher import DecryptionError
from app.security.tenant_keys import TenantKeyDestroyedError, TenantKeyError
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, create_test_credential, unit_context
from tests.conftest import TEST_USER_ID, TestSession


def _stored(tenant: Tenant) -> dict[str, Any]:
    return cast(dict[str, Any], tenant.data_keys)


@pytest.fixture
async def _user(db: AsyncSession) -> None:
    db.add(User(id=TEST_USER_ID, email="b16@test", name="b16"))
    await db.commit()


@pytest.fixture
def _enabled() -> Iterator[None]:
    original = settings.tenant_data_keys_enabled
    settings.tenant_data_keys_enabled = True
    yield
    settings.tenant_data_keys_enabled = original


async def _create(db: AsyncSession, name: str, secret: str) -> Credential:
    return await create_test_credential(
        db,
        CredentialCreation(definition_key="openai", name=name, data={"api_key": secret}),
        unit_context(TEST_USER_ID),
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user")
async def test_default_off_keeps_format_01(db: AsyncSession) -> None:
    assert settings.tenant_data_keys_enabled is False
    cred = await _create(db, "legacy", "sk-legacy")
    assert cipher.blob_version(cred.data_encrypted) == 1
    assert cred.key_id == key_provider.get_active_key_id()
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    assert tenant is not None and tenant.data_keys == {}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user", "_enabled")
async def test_enabled_company_credential_uses_format_02(db: AsyncSession) -> None:
    cred = await _create(db, "tenant", "sk-tenant")
    assert cipher.blob_version(cred.data_encrypted) == 2
    assert cred.key_id == "t:" + cipher.tenant_blob_key_id(cred.data_encrypted)
    assert await credential_service.decrypt_with_external(cred) == {"api_key": "sk-tenant"}
    with pytest.raises(TenantKeyError):
        await credential_service.decrypt_with_external(cred.data_encrypted)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user", "_enabled")
async def test_system_credential_stays_instance_scoped(db: AsyncSession) -> None:
    cred = await create_test_credential(
        db,
        CredentialCreation(definition_key="openai", name="sys", data={"api_key": "sk-sys"}),
        PlatformScope(),
    )
    assert cipher.blob_version(cred.data_encrypted) == 1
    assert await credential_service.decrypt_with_external(cred) == {"api_key": "sk-sys"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user")
async def test_format_01_rows_still_read_when_enabled(db: AsyncSession) -> None:
    cred = await _create(db, "old", "sk-old")
    settings.tenant_data_keys_enabled = True
    try:
        assert await credential_service.decrypt_with_external(cred) == {"api_key": "sk-old"}
    finally:
        settings.tenant_data_keys_enabled = False


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user", "_enabled")
async def test_blob_moved_to_another_row_fails(db: AsyncSession) -> None:
    source = await _create(db, "src", "sk-src")
    target = await _create(db, "dst", "sk-dst")
    target.data_encrypted = source.data_encrypted
    with pytest.raises(DecryptionError):
        await credential_service.decrypt_with_external(target)
    other_tenant = uuid.uuid4()
    db.add(
        Tenant(
            id=other_tenant,
            name="Other",
            slug="other-b16",
            secrets_namespace=f"tenants/{other_tenant}",
        )
    )
    await db.flush()
    await tenant_keys.ensure_active_key(db, other_tenant)
    target.data_encrypted = source.data_encrypted
    target.tenant_id = other_tenant
    with pytest.raises(DecryptionError):
        await credential_service.decrypt_with_external(target)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user", "_enabled")
async def test_company_rotation_re_encrypts_every_row_and_retires_old_key() -> None:
    async with TestSession() as db:
        settings.tenant_data_keys_enabled = False
        legacy = await _create(db, "legacy", "sk-0")
        settings.tenant_data_keys_enabled = True
        rows = [legacy] + [await _create(db, f"r{i}", f"sk-{i + 1}") for i in range(3)]
        await db.commit()
        old_key_id = rows[1].key_id.removeprefix("t:")
        ids = [row.id for row in rows]

    new_key_id = await rotation.rotate_tenant_data_key(
        session_factory=TestSession, tenant_id=TEST_TENANT_ID, batch_size=2
    )
    assert new_key_id != old_key_id

    async with TestSession() as db:
        stored = list(
            (await db.execute(select(Credential).where(Credential.id.in_(ids)))).scalars()
        )
        assert {row.key_id for row in stored} == {f"t:{new_key_id}"}
        assert all(cipher.blob_version(row.data_encrypted) == 2 for row in stored)
        payloads = {
            (await credential_service.decrypt_with_external(row))["api_key"] for row in stored
        }
        assert payloads == {f"sk-{i}" for i in range(4)}
        tenant = await db.get(Tenant, TEST_TENANT_ID)
        assert tenant is not None
        assert _stored(tenant)["keys"][old_key_id]["status"] == "retired"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user", "_enabled")
async def test_destroyed_company_keys_make_rows_unreadable(db: AsyncSession) -> None:
    cred = await _create(db, "gone", "sk-gone")
    await tenant_keys.destroy_tenant_keys(db, TEST_TENANT_ID)
    with pytest.raises(TenantKeyDestroyedError) as error:
        await credential_service.decrypt_with_external(cred)
    assert "sk-gone" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user")
async def test_instance_rotation_job_migrates_01_and_terminates_on_02() -> None:
    async with TestSession() as db:
        legacy = await _create(db, "legacy", "sk-legacy")
        settings.tenant_data_keys_enabled = True
        try:
            sealed = await _create(db, "sealed", "sk-sealed")
            await db.commit()
            ids = (legacy.id, sealed.id)
        finally:
            settings.tenant_data_keys_enabled = False

    settings.tenant_data_keys_enabled = True
    try:
        with patch("app.scheduler.async_session", TestSession):
            assert await rotate_credentials_to_active_key() == 1
            assert await rotate_credentials_to_active_key() == 0
    finally:
        settings.tenant_data_keys_enabled = False

    async with TestSession() as db:
        stored = list(
            (await db.execute(select(Credential).where(Credential.id.in_(ids)))).scalars()
        )
        assert all(cipher.blob_version(row.data_encrypted) == 2 for row in stored)
    with patch("app.scheduler.async_session", TestSession):
        assert await rotate_credentials_to_active_key() == 0
