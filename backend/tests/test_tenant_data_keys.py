"""B-16 — per-company data keys (envelope encryption) and ciphertext format 0x02."""

from __future__ import annotations

import base64
import secrets
import uuid
from collections.abc import Iterator
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.organization import Tenant
from app.security import cipher, key_provider, tenant_keys
from app.security.cipher import DecryptionError
from app.security.tenant_keys import TenantKeyDestroyedError, TenantKeyNotFoundError


def _aad(tenant: uuid.UUID, table: str = "credentials", row: uuid.UUID | None = None) -> bytes:
    return cipher.tenant_aad(
        tenant_id=tenant, table=table, row_id=row or uuid.UUID(int=1), field="data_encrypted"
    )


def _stored(tenant: Tenant) -> dict[str, Any]:
    return cast(dict[str, Any], tenant.data_keys)


@pytest.fixture
def _restore_keys() -> Iterator[None]:
    original = settings.encryption_keys
    yield
    settings.encryption_keys = original
    key_provider.reset_cache()


def _swap_instance_keys(*hex_keys: str) -> None:
    settings.encryption_keys = ",".join(hex_keys)
    key_provider.reset_cache()


async def _tenant(db: AsyncSession) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    db.add(
        Tenant(
            id=tenant_id,
            name="Key company",
            slug=f"key-{tenant_id.hex[:8]}",
            secrets_namespace=f"tenants/{tenant_id}",
        )
    )
    await db.flush()
    return tenant_id


# -- Format 0x02 ---------------------------------------------------------------


def test_format_02_embeds_version_and_company_key_id() -> None:
    tenant = uuid.uuid4()
    blob = cipher.encrypt_tenant(
        "secret", data_key=secrets.token_bytes(32), key_id="a1b2c3d4e5f6", aad=_aad(tenant)
    )
    raw = base64.b64decode(blob)
    assert raw[0:1] == b"\x02"
    assert cipher.blob_version(blob) == 2
    assert cipher.tenant_blob_key_id(blob) == "a1b2c3d4e5f6"
    legacy = cipher.encrypt("secret", key_provider.get_active_key())
    assert cipher.blob_version(legacy) == 1


def test_other_company_key_cannot_decrypt() -> None:
    tenant = uuid.uuid4()
    blob = cipher.encrypt_tenant(
        "secret", data_key=secrets.token_bytes(32), key_id="a1b2c3d4e5f6", aad=_aad(tenant)
    )
    with pytest.raises(DecryptionError):
        cipher.decrypt_tenant(blob, data_key=secrets.token_bytes(32), aad=_aad(tenant))


@pytest.mark.parametrize("swap", ["tenant", "table", "row"])
def test_aad_binds_company_table_and_row(swap: str) -> None:
    key = secrets.token_bytes(32)
    tenant, row = uuid.uuid4(), uuid.uuid4()
    blob = cipher.encrypt_tenant(
        "secret", data_key=key, key_id="a1b2c3d4e5f6", aad=_aad(tenant, row=row)
    )
    assert cipher.decrypt_tenant(blob, data_key=key, aad=_aad(tenant, row=row)) == "secret"
    moved = {
        "tenant": _aad(uuid.uuid4(), row=row),
        "table": _aad(tenant, table="mcp_servers", row=row),
        "row": _aad(tenant, row=uuid.uuid4()),
    }[swap]
    with pytest.raises(DecryptionError):
        cipher.decrypt_tenant(blob, data_key=key, aad=moved)


def test_legacy_decrypt_rejects_format_02_without_leaking() -> None:
    blob = cipher.encrypt_tenant(
        "secret", data_key=secrets.token_bytes(32), key_id="a1b2c3d4e5f6", aad=_aad(uuid.uuid4())
    )
    with pytest.raises(DecryptionError) as error:
        cipher.decrypt(blob, key_provider.get_keys())
    assert "secret" not in str(error.value)


# -- Wrapping with the instance key ----------------------------------------------


def test_wrap_roundtrip_is_bound_to_company_and_key_id() -> None:
    raw = secrets.token_bytes(32)
    tenant = uuid.uuid4()
    wrapped, wrap_key_id = key_provider.wrap_data_key(raw, tenant_id=tenant, key_id="k1", version=1)
    assert raw.hex() not in wrapped and base64.b64encode(raw).decode() not in wrapped
    assert wrap_key_id == key_provider.get_active_key_id()
    assert (
        key_provider.unwrap_data_key(
            wrapped, wrap_key_id=wrap_key_id, tenant_id=tenant, key_id="k1", version=1
        )
        == raw
    )
    with pytest.raises(DecryptionError):
        key_provider.unwrap_data_key(
            wrapped, wrap_key_id=wrap_key_id, tenant_id=uuid.uuid4(), key_id="k1", version=1
        )


# -- Company key lifecycle in tenants.data_keys ----------------------------------


@pytest.mark.asyncio
async def test_ensure_active_key_is_created_once_and_stored_wrapped(db: AsyncSession) -> None:
    tenant_id = await _tenant(db)
    first = await tenant_keys.ensure_active_key(db, tenant_id)
    second = await tenant_keys.ensure_active_key(db, tenant_id)
    assert first.key_id == second.key_id and first.raw == second.raw
    tenant = await db.get(Tenant, tenant_id)
    assert tenant is not None
    stored = _stored(tenant)
    assert stored["active_key_id"] == first.key_id
    entry = stored["keys"][first.key_id]
    assert entry["status"] == "active" and entry["version"] == 1
    assert first.raw.hex() not in str(stored)
    assert first.raw.hex() not in repr(first)


@pytest.mark.asyncio
async def test_rotate_retires_old_key_but_keeps_it_readable(db: AsyncSession) -> None:
    tenant_id = await _tenant(db)
    old = await tenant_keys.ensure_active_key(db, tenant_id)
    new = await tenant_keys.rotate_key(db, tenant_id)
    assert new.key_id != old.key_id
    tenant = await db.get(Tenant, tenant_id)
    assert tenant is not None
    keys = _stored(tenant)["keys"]
    assert keys[old.key_id]["status"] == "retired" and keys[old.key_id]["retired_at"]
    assert keys[new.key_id]["status"] == "active" and keys[new.key_id]["version"] == 2
    assert (await tenant_keys.get_key(db, tenant_id, old.key_id)).raw == old.raw


@pytest.mark.asyncio
async def test_other_company_cannot_resolve_key_id(db: AsyncSession) -> None:
    owner = await _tenant(db)
    other = await _tenant(db)
    key = await tenant_keys.ensure_active_key(db, owner)
    with pytest.raises(TenantKeyNotFoundError):
        await tenant_keys.get_key(db, other, key.key_id)


@pytest.mark.asyncio
async def test_destroyed_keys_cannot_be_used_or_recreated(db: AsyncSession) -> None:
    tenant_id = await _tenant(db)
    old = await tenant_keys.ensure_active_key(db, tenant_id)
    active = await tenant_keys.rotate_key(db, tenant_id)
    assert await tenant_keys.destroy_tenant_keys(db, tenant_id) == 2
    tenant = await db.get(Tenant, tenant_id)
    assert tenant is not None
    assert all("wrapped" not in entry for entry in _stored(tenant)["keys"].values())
    for key_id in (old.key_id, active.key_id):
        with pytest.raises(TenantKeyDestroyedError) as error:
            await tenant_keys.get_key(db, tenant_id, key_id)
        assert active.raw.hex() not in str(error.value)
    with pytest.raises(TenantKeyDestroyedError):
        await tenant_keys.ensure_active_key(db, tenant_id)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_restore_keys")
async def test_instance_key_rotation_only_rewraps_company_keys(db: AsyncSession) -> None:
    key_a, key_b = secrets.token_hex(32), secrets.token_hex(32)
    _swap_instance_keys(key_a)
    tenant_id = await _tenant(db)
    original = await tenant_keys.ensure_active_key(db, tenant_id)
    _swap_instance_keys(key_b, key_a)
    assert await tenant_keys.rewrap_keys(db, tenant_id) == 1
    assert await tenant_keys.rewrap_keys(db, tenant_id) == 0
    _swap_instance_keys(key_b)
    resolved = await tenant_keys.get_key(db, tenant_id, original.key_id)
    assert resolved.raw == original.raw
    tenant = await db.get(Tenant, tenant_id)
    assert tenant is not None
    entry = _stored(tenant)["keys"][original.key_id]
    assert entry["wrap_key_id"] == key_provider.get_active_key_id()


def test_transit_provider_is_rejected_until_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "data_key_provider", "transit")
    with pytest.raises(tenant_keys.TenantKeyError):
        key_provider.wrap_data_key(
            secrets.token_bytes(32), tenant_id=uuid.uuid4(), key_id="k", version=1
        )
