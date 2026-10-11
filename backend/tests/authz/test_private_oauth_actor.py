"""Public OAuth completion rechecks the persisted credential's organization actor."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, TypedDict, assert_never

import pytest
from httpx import AsyncClient
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import registry
from app.credentials.creation import CredentialCreation
from app.credentials.oauth_service import hash_oauth_state
from app.models.credential_oauth_state import CredentialOAuthState
from app.models.organization import Tenant, TenantMember
from app.models.user import User
from tests.authz.unit_scope_fixture import TEST_TENANT_ID, create_test_credential, unit_context
from tests.conftest import TEST_USER_ID


class TokenPayload(TypedDict):
    access_token: str


@pytest.fixture(autouse=True)
async def revoked_callback(db: AsyncSession) -> None:
    user = User(id=TEST_USER_ID, email="oauth-actor@example.com", name="OAuth fixture")
    db.add(user)
    await db.flush()
    credential = await create_test_credential(
        db,
        CredentialCreation(
            definition_key="mcp_oauth2",
            name="Callback fixture",
            data={"access_token_url": "https://id.example/token", "client_id": "fixture"},
        ),
        unit_context(TEST_USER_ID),
    )
    db.add(
        CredentialOAuthState(
            state_hash=hash_oauth_state("dummy-scoped-state"),
            credential_id=credential.id,
            user_id=user.id,
            redirect_uri="http://test/callback",
            origin="credential",
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10),
        )
    )
    await db.commit()


@dataclass(frozen=True, slots=True)
class CallbackCase:
    client: AsyncClient
    db: AsyncSession
    exchanges: list[bool]


@pytest.fixture
def callback_case(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> CallbackCase:
    exchanges: list[bool] = []

    async def exchange(data: Mapping[str, JsonValue]) -> TokenPayload:
        exchanges.append(True)
        return {"access_token": "dummy-token"}

    monkeypatch.setattr(registry.require("mcp_oauth2"), "pre_authentication", exchange)
    return CallbackCase(client, db, exchanges)


@pytest.mark.parametrize("boundary", ["company", "membership", "account"])
async def test_callback_aborts_before_exchange_when_actor_boundary_is_revoked(
    callback_case: CallbackCase, boundary: Literal["company", "membership", "account"]
) -> None:
    client, db = callback_case.client, callback_case.db
    # Given: an unconsumed OAuth state whose company, membership or account is revoked.
    tenant = await db.get(Tenant, TEST_TENANT_ID)
    member = await db.get(TenantMember, (TEST_TENANT_ID, TEST_USER_ID))
    user = await db.get(User, TEST_USER_ID)
    assert tenant is not None and member is not None and user is not None
    match boundary:
        case "company":
            tenant.status = "suspended"
        case "membership":
            member.status = "removed"
        case "account":
            user.is_active = False
        case unreachable:
            assert_never(unreachable)
    await db.commit()
    # When: the valid state attempts to complete through the public callback.
    response = await client.get(
        "/api/oauth2-credential/callback",
        params={"state": "dummy-scoped-state", "code": "dummy-code"},
    )
    # Then: no token is exchanged and the state remains unconsumed.
    assert response.status_code in (403, 404)
    assert callback_case.exchanges == []
    state = await db.scalar(
        select(CredentialOAuthState).where(
            CredentialOAuthState.state_hash == hash_oauth_state("dummy-scoped-state")
        )
    )
    assert state is not None and state.consumed_at is None
