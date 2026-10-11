"""Explicit validated contexts reused by private-resource cross-company scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext, resolve_org_context

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


@dataclass(frozen=True, slots=True)
class PrivateScopes:
    first: OrgContext
    second: OrgContext


@pytest.fixture
async def private_scopes(db: AsyncSession, org_fixture: OrgFixture) -> PrivateScopes:
    first = await resolve_org_context(db, org_fixture.user, cookie=str(org_fixture.first))
    second = await resolve_org_context(db, org_fixture.user, cookie=str(org_fixture.second))
    assert first.user_id == second.user_id
    assert first.org_id != second.org_id and first.tenant_id != second.tenant_id
    return PrivateScopes(first=first, second=second)
