"""Keep accepted AG-UI worker input private and bound to its original command."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.security import cipher, key_provider, tenant_keys


@dataclass(frozen=True)
class DeliveryIdentity:
    tenant_id: UUID
    org_id: UUID
    user_id: UUID
    conversation_id: UUID
    public_run_id: str
    run_id: UUID
    source: str
    parent_run_id: UUID | None
    checkpoint_id: str

    def binding(self) -> str:
        # Ordered JSON keeps opaque public IDs and checkpoint IDs unambiguous.
        return json.dumps(
            [
                "ag_ui_command_receipts.delivery_encrypted.v1",
                str(self.tenant_id),
                str(self.org_id),
                str(self.user_id),
                str(self.conversation_id),
                self.public_run_id,
                str(self.run_id),
                self.source,
                str(self.parent_run_id) if self.parent_run_id is not None else None,
                self.checkpoint_id,
            ],
            separators=(",", ":"),
        )


class InvalidDelivery(Exception):
    """A worker cannot authenticate or decode the accepted command's private input."""


class _Envelope(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    binding: str
    payload: JsonValue


def _instance_delivery_key(key: cipher.CipherKey, binding: str) -> cipher.CipherKey:
    # Format 0x01 has no AAD. Derive a scoped subkey so even a valid ciphertext
    # from another instance-key consumer cannot be opened as a worker command.
    material = hmac.digest(key.raw, binding.encode("ascii"), "sha256")
    return cipher.CipherKey.from_hex(material.hex())


async def seal_delivery(db: AsyncSession, identity: DeliveryIdentity, payload: object) -> str:
    binding = identity.binding()
    try:
        envelope = _Envelope.model_validate({"binding": binding, "payload": payload})
        plaintext = json.dumps(envelope.model_dump(), separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise InvalidDelivery("Invalid AG-UI delivery payload") from None

    if settings.tenant_data_keys_enabled:
        key = await tenant_keys.ensure_active_key(db, identity.tenant_id)
        return cipher.encrypt_tenant(
            plaintext, data_key=key.raw, key_id=key.key_id, aad=binding.encode("ascii")
        )
    return cipher.encrypt(plaintext, _instance_delivery_key(key_provider.get_active_key(), binding))


async def open_delivery(db: AsyncSession, identity: DeliveryIdentity, blob: str) -> JsonValue:
    binding = identity.binding()
    try:
        key_id = cipher.tenant_blob_key_id(blob) if cipher.blob_version(blob) == 2 else None
    except cipher.DecryptionError:
        raise InvalidDelivery("Invalid AG-UI delivery") from None

    key = None
    if key_id is not None:
        try:
            key = await tenant_keys.get_key(db, identity.tenant_id, key_id)
        except (tenant_keys.TenantKeyDestroyedError, tenant_keys.TenantKeyNotFoundError):
            raise InvalidDelivery("Invalid AG-UI delivery") from None

    try:
        if key is not None:
            plaintext = cipher.decrypt_tenant(blob, data_key=key.raw, aad=binding.encode("ascii"))
        else:
            keys = [
                _instance_delivery_key(instance, binding) for instance in key_provider.get_keys()
            ]
            plaintext = cipher.decrypt(blob, keys)
        envelope = _Envelope.model_validate_json(plaintext)
    except (cipher.DecryptionError, ValidationError, UnicodeError, RecursionError):
        raise InvalidDelivery("Invalid AG-UI delivery") from None
    if envelope.binding != binding:
        raise InvalidDelivery("Invalid AG-UI delivery")
    return envelope.payload
