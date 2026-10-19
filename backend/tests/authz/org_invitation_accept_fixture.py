"""Shared setup for organization invitation-acceptance API tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization_group import Group
from tests.authz.test_org_groups_api import grant_admin
from tests.conftest import AuthSession, register_session

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture


@dataclass(frozen=True, slots=True)
class AcceptanceFixture:
    id: UUID
    token: str
    recipient_id: UUID
    session: AuthSession
    group_id: UUID


async def pending_invitation(
    db: AsyncSession, client: AsyncClient, fixture: OrgFixture
) -> AcceptanceFixture:
    await grant_admin(db, fixture)
    group = await db.scalar(select(Group).where(Group.org_id == fixture.first))
    assert group is not None
    created = await client.post(
        f"/api/orgs/{fixture.first}/invitations",
        json={"email": "recipient@example.com", "role": "builder", "group_ids": [str(group.id)]},
        headers=fixture.session.headers(),
    )
    assert created.status_code == 201
    session = await register_session(client, email="recipient@example.com", name="Recipient")
    return AcceptanceFixture(
        id=UUID(created.json()["id"]),
        token=created.json()["token"],
        recipient_id=UUID(session.body["user"]["id"]),
        session=session,
        group_id=group.id,
    )
