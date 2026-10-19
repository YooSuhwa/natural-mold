"""D-04 create/delete queue and apply tuples before responding (7.2.1, 8.6 item 2)."""

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.authz import AuthzOutbox
from tests.authz.d04_support import AuthzSwitch, OwnerStore, authz, seed_scope
from tests.conftest import TestSession

__all__ = ["authz"]  # fixture re-export


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession, monkeypatch) -> None:
    monkeypatch.setattr("app.database.async_session", TestSession)  # outbox apply sessions
    await seed_scope(db)


@pytest.mark.parametrize(
    ("collection", "kind", "body"),
    [
        ("/api/tools", "tool", {"definition_key": "http_request", "name": "n", "parameters": {}}),
        (
            "/api/mcp-servers",
            "mcp_server",
            {"name": "m", "transport": "streamable_http", "url": "https://mcp.example.test"},
        ),
        (
            "/api/credentials",
            "credential",
            {"definition_key": "http_bearer", "name": "c", "data": {"token": "x"}},
        ),
    ],
)
async def test_enforce_create_then_get_reads_its_own_write(
    client: AsyncClient, authz: AuthzSwitch, collection: str, kind: str, body: dict
) -> None:
    store = OwnerStore()
    authz.use(client=store)
    created = await client.post(collection, json=body)
    assert created.status_code == 201, created.text
    obj = f"{kind}:{created.json()['id']}"
    assert any(t.relation == "owner" and t.object == obj for t in store.stored())
    assert (await client.get(f"{collection}/{created.json()['id']}")).status_code == 200
    assert (await client.delete(f"{collection}/{created.json()['id']}")).status_code == 204
    assert not [t for t in store.stored() if t.object == obj]


async def test_legacy_create_queues_nothing(client: AsyncClient) -> None:
    body = {"definition_key": "http_request", "name": "n", "parameters": {}}
    assert (await client.post("/api/tools", json=body)).status_code == 201
    async with TestSession() as db:
        assert await db.scalar(select(func.count()).select_from(AuthzOutbox)) == 0
