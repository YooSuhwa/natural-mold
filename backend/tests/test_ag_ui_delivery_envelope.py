"""Private worker inputs stay bound to one accepted command, including after key rotation."""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.organization import Tenant
from app.security import cipher, key_provider, tenant_keys
from app.services.ag_ui_delivery_envelope import (
    DeliveryIdentity,
    InvalidDelivery,
    open_delivery,
    seal_delivery,
)


@pytest.fixture
async def identity(db: AsyncSession) -> DeliveryIdentity:
    tenant_id = uuid4()
    db.add(
        Tenant(
            id=tenant_id,
            name="Receipt",
            slug=f"receipt-{tenant_id.hex[:8]}",
            secrets_namespace=f"tenants/{tenant_id}",
        )
    )
    await db.flush()
    return DeliveryIdentity(
        tenant_id=tenant_id,
        org_id=uuid4(),
        user_id=uuid4(),
        conversation_id=uuid4(),
        public_run_id="x|y",
        run_id=uuid4(),
        source="resume",
        parent_run_id=uuid4(),
        checkpoint_id="검토시점",
    )


@pytest.mark.parametrize("tenant_keys_enabled", [False, True])
async def test_only_bound_command_can_read_payload(
    db: AsyncSession,
    identity: DeliveryIdentity,
    tenant_keys_enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "tenant_data_keys_enabled", tenant_keys_enabled)
    payload = {"interruptIds": ["i-12"], "response": [None, False, "비공개 값", 42]}
    blob = await seal_delivery(db, identity, payload)
    assert cipher.blob_version(blob) == (2 if tenant_keys_enabled else 1)
    # Rollback of the flag still reads work accepted under the other configuration.
    monkeypatch.setattr(settings, "tenant_data_keys_enabled", not tenant_keys_enabled)
    assert await open_delivery(db, identity, blob) == payload
    for moved in (
        replace(identity, tenant_id=uuid4()),
        replace(identity, org_id=uuid4()),
        replace(identity, user_id=uuid4()),
        replace(identity, conversation_id=uuid4()),
        replace(identity, public_run_id="x"),
        replace(identity, run_id=uuid4()),
        replace(identity, source="edit"),
        replace(identity, parent_run_id=None),
        replace(identity, checkpoint_id="기타"),
    ):
        with pytest.raises(InvalidDelivery, match="^Invalid AG-UI delivery$"):
            await open_delivery(db, moved, blob)
    damaged = bytearray(base64.b64decode(blob))
    damaged[-1] ^= 1
    altered = base64.b64encode(damaged).decode("ascii")
    for unreadable in (
        altered,
        "",
        "malformed",
        cipher.encrypt(
            json.dumps({"binding": identity.binding(), "payload": payload}),
            key_provider.get_active_key(),
        ),
    ):
        with pytest.raises(InvalidDelivery, match="^Invalid AG-UI delivery$"):
            await open_delivery(db, identity, unreadable)


async def test_instance_rotation_reads_existing_delivery(
    db: AsyncSession,
    identity: DeliveryIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = secrets.token_hex(32)
    monkeypatch.setattr(settings, "tenant_data_keys_enabled", False)
    monkeypatch.setattr(settings, "encryption_keys", first)
    key_provider.reset_cache()
    try:
        blob = await seal_delivery(db, identity, "approved")
        monkeypatch.setattr(settings, "encryption_keys", f"{secrets.token_hex(32)},{first}")
        key_provider.reset_cache()
        assert await open_delivery(db, identity, blob) == "approved"
    finally:
        key_provider.reset_cache()


async def test_company_key_rotation_and_destruction(
    db: AsyncSession,
    identity: DeliveryIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "tenant_data_keys_enabled", True)
    blob = await seal_delivery(db, identity, None)
    await tenant_keys.rotate_key(db, identity.tenant_id)
    assert await open_delivery(db, identity, blob) is None
    await tenant_keys.destroy_tenant_keys(db, identity.tenant_id)
    with pytest.raises(InvalidDelivery):
        await open_delivery(db, identity, blob)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), object(), {1: "non-string key"}])
async def test_payload_requires_json(
    db: AsyncSession, identity: DeliveryIdentity, invalid: object
) -> None:
    with pytest.raises(InvalidDelivery, match="^Invalid AG-UI delivery payload$"):
        await seal_delivery(db, identity, invalid)


async def test_key_store_outage_remains_retryable(
    db: AsyncSession,
    identity: DeliveryIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "tenant_data_keys_enabled", True)
    blob = await seal_delivery(db, identity, "response")

    async def unavailable(*args: object) -> None:
        raise OperationalError(None, None, ConnectionError("offline"))

    monkeypatch.setattr(tenant_keys, "get_key", unavailable)
    with pytest.raises(OperationalError):
        await open_delivery(db, identity, blob)
