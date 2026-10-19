"""One tenant's connection lifecycle cannot delete a global IdP identity."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from schema_drafts.org_authz.schema import schema_metadata

pytestmark = pytest.mark.integration


async def test_two_tenant_shared_issuer_identity_survives_origin_connection_removal() -> None:
    schema = "authz_sso_" + uuid4().hex
    engine = create_async_engine(os.environ["INTEGRATION_DATABASE_URL"])
    metadata = schema_metadata()
    users, tenants = metadata.tables["users"], metadata.tables["tenants"]
    connections, identities = metadata.tables["sso_connections"], metadata.tables["user_identities"]
    members = metadata.tables["tenant_members"]
    user, tenant_a, tenant_b, conn_a, conn_b, identity = (uuid4() for _ in range(6))
    try:
        async with engine.begin() as db:
            await db.execute(text(f'CREATE SCHEMA "{schema}"'))
            await db.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await db.run_sync(
                lambda conn: metadata.create_all(
                    conn, tables=[users, tenants, connections, identities, members]
                )
            )
            await db.execute(insert(users).values(id=user, email="sso@example.test", name="Shared"))
            await db.execute(
                insert(tenants),
                [
                    {"id": tenant_a, "name": "A", "slug": "a", "secrets_namespace": "a"},
                    {"id": tenant_b, "name": "B", "slug": "b", "secrets_namespace": "b"},
                ],
            )
            await db.execute(
                insert(connections),
                [
                    {
                        "id": conn_a,
                        "tenant_id": tenant_a,
                        "issuer": "https://idp.example.test",
                        "status": "active",
                    },
                    {
                        "id": conn_b,
                        "tenant_id": tenant_b,
                        "issuer": "https://idp.example.test",
                        "status": "active",
                    },
                ],
            )
            await db.execute(
                insert(members),
                [
                    {"tenant_id": tenant_a, "user_id": user, "status": "active"},
                    {"tenant_id": tenant_b, "user_id": user, "status": "active"},
                ],
            )
            await db.execute(
                insert(identities).values(
                    id=identity,
                    user_id=user,
                    connection_id=conn_a,
                    issuer="https://idp.example.test",
                    subject="stable-subject",
                )
            )
            # The current connection's verified issuer supplies identity lookup;
            # historical connection_id never controls tenant B's acceptance.
            lookup = (
                select(identities.c.user_id)
                .join(connections, connections.c.issuer == identities.c.issuer)
                .join(
                    members,
                    (members.c.tenant_id == connections.c.tenant_id)
                    & (members.c.user_id == identities.c.user_id),
                )
                .where(
                    connections.c.id == conn_b,
                    connections.c.tenant_id == tenant_b,
                    connections.c.status == "active",
                    identities.c.subject == "stable-subject",
                    members.c.status == "active",
                )
            )
            await db.execute(
                update(connections).where(connections.c.id == conn_a).values(status="disabled")
            )
            assert await db.scalar(lookup) == user
            await db.execute(delete(connections).where(connections.c.id == conn_a))
            await db.execute(delete(tenants).where(tenants.c.id == tenant_a))
            assert await db.scalar(lookup) == user
            assert (
                await db.scalar(
                    select(identities.c.connection_id).where(identities.c.id == identity)
                )
                is None
            )
            await db.execute(
                update(connections).where(connections.c.id == conn_b).values(status="disabled")
            )
            assert await db.scalar(lookup) is None
            await db.execute(
                update(connections).where(connections.c.id == conn_b).values(status="active")
            )
            await db.execute(
                update(members).where(members.c.tenant_id == tenant_b).values(status="removed")
            )
            assert await db.scalar(lookup) is None
            await db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        await engine.dispose()
