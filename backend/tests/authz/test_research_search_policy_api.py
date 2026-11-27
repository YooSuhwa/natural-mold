"""Organization search policy governance, destination validation and visibility."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.organization import Organization
from app.schemas.research_search import ResearchSearchPolicy
from tests.authz.test_org_groups_api import grant_admin

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]

ENDPOINT = "https://approved-search.example/search"


async def test_admin_can_version_and_revoke_search_policy(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    await grant_admin(db, org_fixture)
    organization = await db.get(Organization, org_fixture.first)
    assert organization is not None
    organization.settings = {"unrelated": True}
    await db.commit()
    path = f"/api/orgs/{org_fixture.first}/research-search-policy"
    original = await raw_client.get(path)
    assert original.status_code == 200
    assert original.json()["policy"] is None

    payload = {
        "providers": ["tavily", "searxng"],
        "searxng_endpoint": ENDPOINT,
        "monthly_attempt_limit": 2_147_483_647,
    }
    headers = {**org_fixture.session.headers(), "If-Match": original.headers["etag"]}
    changed = await raw_client.put(path, headers=headers, json=payload)
    assert changed.status_code == 200
    assert changed.json()["policy"] == payload
    subsequent = await raw_client.get(path)
    assert subsequent.headers["etag"] == changed.headers["etag"] != original.headers["etag"]
    assert subsequent.json() == changed.json()
    await db.refresh(organization)
    assert organization.settings == {"unrelated": True, "research_search": payload}
    audit = await db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "org.policy_update", AuditEvent.target_id == str(org_fixture.first)
        )
    )
    assert audit is not None
    assert audit.tenant_id == org_fixture.tenant and audit.org_id == org_fixture.first
    assert audit.event_metadata is not None
    assert audit.event_metadata["providers"] == ["tavily", "searxng"]
    assert ENDPOINT not in str(audit.event_metadata)

    unchanged = await raw_client.put(
        path, headers={**headers, "If-Match": changed.headers["etag"]}, json=payload
    )
    assert unchanged.headers["etag"] == changed.headers["etag"]
    revoke = {"providers": [], "monthly_attempt_limit": 0}
    assert (await raw_client.put(path, headers=headers, json=revoke)).status_code == 409
    revoked = await raw_client.put(
        path, headers={**headers, "If-Match": changed.headers["etag"]}, json=revoke
    )
    assert revoked.status_code == 200
    assert revoked.json()["policy"] == {**revoke, "searxng_endpoint": None}


async def test_members_and_foreign_users_cannot_read_or_write_search_policy(
    db: AsyncSession, raw_client: AsyncClient, org_fixture: OrgFixture
) -> None:
    payload = {"providers": ["searxng"], "searxng_endpoint": ENDPOINT, "monthly_attempt_limit": 7}
    organization = await db.get(Organization, org_fixture.first)
    assert organization is not None
    organization.settings = {"research_search": payload}
    await db.commit()
    for org_id, expected in ((org_fixture.first, 403), (org_fixture.foreign, 404)):
        path = f"/api/orgs/{org_id}/research-search-policy"
        read = await raw_client.get(path)
        write = await raw_client.put(path, headers=org_fixture.session.headers(), json=payload)
        assert read.status_code == write.status_code == expected
        assert ENDPOINT not in read.text and ENDPOINT not in write.text
    org_details = await raw_client.get(f"/api/orgs/{org_fixture.first}")
    assert org_details.status_code == 200
    assert "research_search" not in org_details.json()["settings"]
    assert ENDPOINT not in org_details.text
    await grant_admin(db, org_fixture)
    no_csrf = await raw_client.put(
        f"/api/orgs/{org_fixture.first}/research-search-policy", json=payload
    )
    assert no_csrf.status_code == 403 and no_csrf.json()["error"]["code"] == "csrf_mismatch"
    await db.refresh(organization)
    assert organization.settings["research_search"] == payload


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://search.example/search",
        "https://user:secret@search.example/search",
        "https://search.example/search?key=secret",
        "https://search.example/search#fragment",
        "https://search.example/\nsearch",
        "https://search.example:65536/search",
        "https://*.example/search",
        "https://169.254.169.254/search",
        "https://[::ffff:169.254.169.254]/search",
    ],
)
def test_invalid_search_destinations_are_rejected(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        ResearchSearchPolicy(
            providers=("searxng",), searxng_endpoint=endpoint, monthly_attempt_limit=10
        )


@pytest.mark.parametrize(
    "input_values",
    [
        {"providers": ["tavily", "tavily"], "monthly_attempt_limit": 1},
        {"providers": ["other"], "monthly_attempt_limit": 1},
        {"providers": ["searxng"], "monthly_attempt_limit": 1},
        {"providers": ["tavily"], "monthly_attempt_limit": -1},
        {"providers": ["tavily"], "monthly_attempt_limit": 2_147_483_648},
        {"providers": ["tavily"], "monthly_attempt_limit": True},
        {"providers": ["tavily"], "monthly_attempt_limit": 0, "searxng_endpoint": ENDPOINT},
    ],
)
def test_inconsistent_search_policies_are_rejected(input_values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ResearchSearchPolicy.model_validate(input_values)
