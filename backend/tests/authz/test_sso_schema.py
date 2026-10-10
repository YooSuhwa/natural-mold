"""Enterprise connection storage rejects plaintext LDAP and preserves identity."""

from uuid import uuid4

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from schema_drafts.org_authz.schema import schema_metadata


async def test_ldap_configuration_is_complete_and_transport_is_encrypted() -> None:
    metadata = schema_metadata()
    engine = create_async_engine("sqlite+aiosqlite://")
    tenant = uuid4()
    connection = metadata.tables["sso_connections"]
    try:
        async with engine.begin() as db:
            await db.run_sync(metadata.create_all)
            await db.execute(
                insert(metadata.tables["tenants"]).values(
                    id=tenant, name="LDAP", slug="ldap", secrets_namespace="ldap"
                )
            )
            values = {
                "tenant_id": tenant,
                "issuer": "ldap:company-connection",
                "protocol": "ldap",
                "status": "active",
                "ldap_server_url": "ldap://directory.example.test",
                "ldap_start_tls": True,
                "ldap_bind_dn": "cn=service,dc=example,dc=test",
                "ldap_bind_password_encrypted": "dummy-ciphertext",
                "key_id": "dummy-key",
                "ldap_user_base_dn": "dc=example,dc=test",
                "ldap_user_filter": "(uid={username})",
                "ldap_group_base_dn": "ou=groups,dc=example,dc=test",
                "ldap_group_filter": "(member={dn})",
                "ldap_attribute_mapping": {"subject": "entryUUID", "email": "mail"},
                "ldap_ca_certificate": "dummy-ca",
            }
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await db.execute(
                        insert(connection).values(**(values | {"ldap_start_tls": False}))
                    )
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await db.execute(
                        insert(connection).values(
                            **(values | {"ldap_bind_password_encrypted": None})
                        )
                    )
            await db.execute(insert(connection).values(**values))
            await db.execute(
                insert(connection).values(
                    **(
                        values
                        | {
                            "ldap_server_url": "ldaps://directory.example.test",
                            "ldap_start_tls": False,
                        }
                    )
                )
            )
    finally:
        await engine.dispose()
