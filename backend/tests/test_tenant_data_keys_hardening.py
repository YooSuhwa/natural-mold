"""B-16 review hardening: per-company rewrap isolation, 0x01 closure, rotation races."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import credential_resolution
from app.authz.platform_scope import PlatformScope
from app.config import settings
from app.credentials import rotation, tenant_encryption
from app.credentials.creation import CredentialCreation
from app.models.credential import Credential
from app.models.organization import Tenant
from app.models.user import User
from app.scheduler import rotate_credentials_to_active_key
from app.security import cipher, key_provider, tenant_keys
from app.security.cipher import TenantKeyError
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, create_test_credential, unit_context
from tests.conftest import TEST_USER_ID, TestSession


def _stored(tenant: Tenant | None) -> dict[str, Any]:
    assert tenant is not None
    return cast(dict[str, Any], tenant.data_keys)


@pytest.fixture
async def _user(db: AsyncSession) -> None:
    db.add(User(id=TEST_USER_ID, email="b16h@test", name="b16h"))
    await db.commit()


@pytest.fixture
def _restore() -> Iterator[None]:
    keys, flag = settings.encryption_keys, settings.tenant_data_keys_enabled
    yield
    settings.encryption_keys, settings.tenant_data_keys_enabled = keys, flag
    key_provider.reset_cache()
    tenant_keys.clear_unwrap_cache()


def _instance_keys(*hex_keys: str) -> None:
    settings.encryption_keys = ",".join(hex_keys)
    key_provider.reset_cache()


async def _create(db: AsyncSession, name: str, secret: str) -> Credential:
    return await create_test_credential(
        db,
        CredentialCreation(definition_key="openai", name=name, data={"api_key": secret}),
        unit_context(TEST_USER_ID),
    )


async def _other_tenant(db: AsyncSession) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    db.add(
        Tenant(
            id=tenant_id,
            name="Other",
            slug=f"o-{tenant_id.hex[:8]}",
            secrets_namespace=f"tenants/{tenant_id}",
        )
    )
    await db.flush()
    return tenant_id


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user", "_restore")
async def test_broken_company_does_not_block_rewrap_or_01_rotation() -> None:
    key_a, key_b = secrets.token_hex(32), secrets.token_hex(32)
    _instance_keys(key_a)
    async with TestSession() as db:
        broken, healthy = await _other_tenant(db), await _other_tenant(db)
        await tenant_keys.ensure_active_key(db, broken)
        healthy_key = await tenant_keys.ensure_active_key(db, healthy)
        tenant = await db.get(Tenant, broken)
        state = _stored(tenant)
        state["keys"][state["active_key_id"]]["wrap_key_id"] = "00000000"
        assert tenant is not None
        tenant.data_keys = dict(state)
        legacy = await _create(db, "legacy", "sk-legacy")
        await db.commit()
    _instance_keys(key_b, key_a)
    with patch("app.scheduler.async_session", TestSession):
        assert await rotate_credentials_to_active_key() == 1
    async with TestSession() as db:
        entry = _stored(await db.get(Tenant, healthy))["keys"][healthy_key.key_id]
        assert entry["wrap_key_id"] == key_provider.get_active_key_id()
        row = await db.get(Credential, legacy.id)
        assert row is not None and row.key_id == key_provider.get_active_key_id()


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user", "_restore")
async def test_01_is_rejected_after_company_migration_closes_it() -> None:
    async with TestSession() as db:
        legacy = await _create(db, "legacy", "sk-legacy")
        system = await create_test_credential(
            db,
            CredentialCreation(definition_key="openai", name="sys", data={"api_key": "sk-sys"}),
            PlatformScope(),
        )
        await db.commit()
        settings.tenant_data_keys_enabled = True
        assert await tenant_encryption.open_payload(legacy) == {"api_key": "sk-legacy"}
        assert not _stored(await db.get(Tenant, TEST_TENANT_ID)).get("legacy_01_closed_at")
    with patch("app.scheduler.async_session", TestSession):
        await rotate_credentials_to_active_key()
    async with TestSession() as db:
        assert _stored(await db.get(Tenant, TEST_TENANT_ID))["legacy_01_closed_at"]
        row = await db.get(Credential, legacy.id)
        sys_row = await db.get(Credential, system.id)
        assert row is not None and sys_row is not None
        assert await tenant_encryption.open_payload(sys_row) == {"api_key": "sk-sys"}
        row.data_encrypted = sys_row.data_encrypted
        with pytest.raises(TenantKeyError):
            await tenant_encryption.open_payload(row)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user", "_restore")
async def test_concurrent_company_rotations_terminate_on_final_key() -> None:
    settings.tenant_data_keys_enabled = True
    async with TestSession() as db:
        ids = [(await _create(db, f"r{i}", f"sk-{i}")).id for i in range(5)]
        await db.commit()

    async def _rotate() -> str:
        return await rotation.rotate_tenant_data_key(
            session_factory=TestSession, tenant_id=TEST_TENANT_ID, batch_size=1
        )

    await asyncio.wait_for(asyncio.gather(_rotate(), _rotate()), timeout=20)
    async with TestSession() as db:
        active = _stored(await db.get(Tenant, TEST_TENANT_ID))["active_key_id"]
        rows = list(await db.scalars(select(Credential).where(Credential.id.in_(ids))))
        assert {row.key_id for row in rows} == {f"t:{active}"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user", "_restore")
async def test_superseded_rotation_stops_instead_of_looping() -> None:
    settings.tenant_data_keys_enabled = True
    async with TestSession() as db:
        await _create(db, "r", "sk-r")
        stale = await tenant_keys.rotate_key(db, TEST_TENANT_ID)
        await tenant_keys.rotate_key(db, TEST_TENANT_ID)
        await db.commit()
    moved = await asyncio.wait_for(
        rotation.re_encrypt_tenant_rows(
            session_factory=TestSession, tenant_id=TEST_TENANT_ID, key=stale, batch_size=1
        ),
        timeout=10,
    )
    assert moved == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("_user", "_restore")
async def test_runtime_llm_credential_path_reads_format_02() -> None:
    settings.tenant_data_keys_enabled = True
    async with TestSession() as db:
        cred = await _create(db, "llm", "sk-llm")
        assert cipher.blob_version(cred.data_encrypted) == 2
        assert await credential_resolution._decrypt_api_key(cred) == "sk-llm"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_restore")
async def test_unwrap_cache_is_dropped_on_destroy(db: AsyncSession) -> None:
    tenant_id = await _other_tenant(db)
    key = await tenant_keys.ensure_active_key(db, tenant_id)
    with patch.object(key_provider, "unwrap_data_key", wraps=key_provider.unwrap_data_key) as spy:
        await tenant_keys.get_key(db, tenant_id, key.key_id)
        await tenant_keys.get_key(db, tenant_id, key.key_id)
        assert spy.call_count <= 1
    await tenant_keys.destroy_tenant_keys(db, tenant_id)
    with pytest.raises(tenant_keys.TenantKeyDestroyedError):
        await tenant_keys.get_key(db, tenant_id, key.key_id)
    assert not tenant_keys.cached_key_count(tenant_id)


@pytest.mark.asyncio
async def test_key_id_collision_is_retried(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = await _other_tenant(db)
    first = await tenant_keys.ensure_active_key(db, tenant_id)
    ids = iter([first.key_id, "abcdefabcdef"])
    monkeypatch.setattr(tenant_keys.secrets, "token_hex", lambda _n: next(ids))
    assert (await tenant_keys.rotate_key(db, tenant_id)).key_id == "abcdefabcdef"


def test_wrap_binds_version_and_unwrap_checks_length() -> None:
    tenant = uuid.uuid4()
    wrapped, wrap_id = key_provider.wrap_data_key(
        secrets.token_bytes(32), tenant_id=tenant, key_id="k", version=1
    )
    with pytest.raises(cipher.DecryptionError):
        key_provider.unwrap_data_key(
            wrapped, wrap_key_id=wrap_id, tenant_id=tenant, key_id="k", version=2
        )
    short, short_id = key_provider.wrap_data_key(
        secrets.token_bytes(16), tenant_id=tenant, key_id="k", version=1
    )
    with pytest.raises(TenantKeyError):
        key_provider.unwrap_data_key(
            short, wrap_key_id=short_id, tenant_id=tenant, key_id="k", version=1
        )
