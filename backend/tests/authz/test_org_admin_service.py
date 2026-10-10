"""A metadata read context cannot be reused as authorization for a service mutation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_control import OrgControlTarget, load_org_control
from app.exceptions import AppError
from app.schemas.org import OrganizationPatch
from app.services.orgs.info import OrgMutationRequest, update_organization_info
from app.services.orgs.versioning import etag

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture

pytest_plugins = ["tests.authz.org_context_fixture"]


async def test_read_context_never_authorizes_direct_policy_service_call(
    db: AsyncSession,
    org_fixture: OrgFixture,
) -> None:
    context = await load_org_control(db, org_fixture.user, OrgControlTarget(org_fixture.first))
    original = context.organization.name
    mutation = OrgMutationRequest(
        db=db,
        request=Request({"type": "http", "method": "PATCH", "path": "/direct", "headers": []}),
        expected_version=etag(context.organization.updated_at),
    )
    # Given: a legitimate read context with a correct version but no governance grant.
    # When: an internal caller attempts the mutation directly, without a router dependency.
    with pytest.raises(AppError) as denied:
        await update_organization_info(mutation, context, OrganizationPatch(name="Unauthorized"))
    # Then: the shared service itself denies the write and leaves the data unchanged.
    assert denied.value.code == "permission_required"
    await db.refresh(context.organization)
    assert context.organization.name == original
