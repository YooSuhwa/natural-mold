"""Seal/open the connection secret (OIDC client secret or LDAP bind password).

The company data key (format 0x02, AAD pinned to company/table/row/column) when
``TENANT_DATA_KEYS_ENABLED`` is on, the instance key (0x01) otherwise — the same rule
as company credentials. Plaintext never leaves this module except to the IdP call.
"""

from typing import Final, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.organization_identity import SsoConnection
from app.security import cipher, tenant_keys
from app.security.key_provider import get_active_key, get_keys

SecretField = Literal["client_secret_encrypted", "ldap_bind_password_encrypted"]
TENANT_KEY_PREFIX: Final = "t:"
_TABLE: Final = "sso_connections"


def secret_field(connection: SsoConnection) -> SecretField:
    return (
        "ldap_bind_password_encrypted"
        if connection.protocol == "ldap"
        else "client_secret_encrypted"
    )


def _aad(connection: SsoConnection, field: SecretField) -> bytes:
    return cipher.tenant_aad(
        tenant_id=connection.tenant_id, table=_TABLE, row_id=connection.id, field=field
    )


async def seal_secret(db: AsyncSession, connection: SsoConnection, plaintext: str) -> None:
    """Encrypt into the protocol's column; the row must already have its id."""
    field = secret_field(connection)
    if bool(getattr(settings, "tenant_data_keys_enabled", False)):
        key = await tenant_keys.ensure_active_key(db, connection.tenant_id)
        blob = cipher.encrypt_tenant(
            plaintext, data_key=key.raw, key_id=key.key_id, aad=_aad(connection, field)
        )
        key_id = TENANT_KEY_PREFIX + key.key_id
    else:
        active = get_active_key()
        blob, key_id = cipher.encrypt(plaintext, active), active.key_id
    setattr(connection, field, blob)
    connection.key_id = key_id


async def open_secret(db: AsyncSession, connection: SsoConnection) -> str | None:
    field = secret_field(connection)
    blob = getattr(connection, field)
    if not blob:
        return None
    if cipher.blob_version(blob) != 2:
        return cipher.decrypt(blob, get_keys())
    key = await tenant_keys.get_key(db, connection.tenant_id, cipher.tenant_blob_key_id(blob))
    return cipher.decrypt_tenant(blob, data_key=key.raw, aad=_aad(connection, field))
