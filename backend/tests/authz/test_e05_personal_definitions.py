"""E-05: personal account types are marked on the credential definitions (plan 10.4, Q4)."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials.personal import is_personal_definition
from app.credentials.registry import registry
from tests.authz.e01_support import add_credential, seed

PERSONAL = {"google_workspace_oauth2", "srt_account", "ktx_account", "foresttrip_account"}


@pytest.fixture(autouse=True)
async def _scope(db: AsyncSession) -> None:
    await seed(db)


def test_the_q4_list_is_exactly_the_personal_definitions() -> None:
    marked = {d.key for d in registry.all() if d.personal}
    # mcp_oauth2 is personal by default; the "조직 공용 계정" marking does not exist yet.
    assert marked == PERSONAL | {"mcp_oauth2"}
    for key in ("openai", "anthropic", "http_bearer", "http_api_key", "mcp_secret", "dart_api"):
        assert not is_personal_definition(key)
    assert not is_personal_definition("unknown_definition")


async def test_catalog_exposes_the_personal_flag(client: AsyncClient) -> None:
    listed = (await client.get("/api/credential-types")).json()
    assert {item["key"]: item["personal"] for item in listed}["srt_account"] is True
    one = (await client.get("/api/credential-types/http_bearer")).json()
    assert one["personal"] is False


async def test_credential_list_and_detail_carry_the_personal_flag(
    client: AsyncClient, db: AsyncSession
) -> None:
    personal = await add_credential(db, definition_key="ktx_account")
    plain = await add_credential(db, definition_key="http_bearer")
    listed = (await client.get("/api/credentials")).json()
    flags = {item["id"]: item["personal"] for item in listed}
    assert (flags[str(personal.id)], flags[str(plain.id)]) == (True, False)
    detail = (await client.get(f"/api/credentials/{personal.id}")).json()
    assert detail["personal"] is True
