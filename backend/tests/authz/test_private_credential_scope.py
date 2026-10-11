"""An owner cannot read or mutate private credentials from another active org."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never
from uuid import UUID

import pytest
from httpx import AsyncClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credential import Credential

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


class CredentialReference(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID


@dataclass(frozen=True, slots=True)
class ScopedCredentialCase:
    client: AsyncClient
    organization: OrgFixture
    credential_id: UUID


@pytest.fixture
async def private_credential(raw_client: AsyncClient, org_fixture: OrgFixture) -> UUID:
    created = await raw_client.post(
        "/api/credentials",
        json={
            "definition_key": "http_bearer",
            "name": "Scoped fixture",
            "data": {"token": "dummy-only"},
        },
        headers=org_fixture.session.headers(),
    )
    assert created.status_code == 201, created.text
    return CredentialReference.model_validate_json(created.content).id


@pytest.fixture
def credential_case(
    raw_client: AsyncClient, org_fixture: OrgFixture, private_credential: UUID
) -> ScopedCredentialCase:
    return ScopedCredentialCase(raw_client, org_fixture, private_credential)


async def test_create_persists_explicit_current_org_and_company(
    db: AsyncSession, private_credential: UUID, private_scopes: PrivateScopes
) -> None:
    # Given: a credential created through the authenticated request in organization A.
    # When: its persisted scope is loaded independently of the router response.
    credential = await db.get(Credential, private_credential)
    assert credential is not None
    # Then: the caller and exact company/org are stored, with no default-org substitution.
    assert (credential.org_id, credential.tenant_id, credential.user_id) == (
        private_scopes.first.org_id,
        private_scopes.first.tenant_id,
        private_scopes.first.user_id,
    )


@pytest.mark.parametrize("operation", ["list", "get", "delete"])
async def test_other_org_is_invisible_for_same_owner(
    credential_case: ScopedCredentialCase, operation: Literal["list", "get", "delete"]
) -> None:
    # Given: the same owner selects organization B after creating a credential in A.
    raw_client, org_fixture = credential_case.client, credential_case.organization
    private_credential = credential_case.credential_id
    switched = await raw_client.post(
        "/api/me/active-org",
        json={"org_id": str(org_fixture.second)},
        headers=org_fixture.session.headers(),
    )
    assert switched.status_code == 200
    # When: B's credential surface is queried or used to delete A's credential.
    match operation:
        case "list":
            response = await raw_client.get("/api/credentials")
            # Then: the other org contributes zero rows.
            assert response.status_code == 200
            assert response.json() == []
        case "get":
            response = await raw_client.get(f"/api/credentials/{private_credential}")
            # Then: the owned but foreign-org ID is indistinguishable from a missing ID.
            assert response.status_code == 404
        case "delete":
            response = await raw_client.delete(
                f"/api/credentials/{private_credential}", headers=org_fixture.session.headers()
            )
            # Then: organization switching cannot authorize deleting the old org's row.
            assert response.status_code == 404
        case unreachable:
            assert_never(unreachable)
