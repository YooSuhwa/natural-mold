"""Captured private readers refresh authority before metadata or SQL effects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_api import service
from app.exceptions import AppError
from app.models.organization import OrganizationMember, Tenant
from app.models.user import User

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase
pytest_plugins = ["tests.authz.test_private_agent_api_scope"]
State = Literal["active", "membership_removed", "account_inactive", "tenant_suspended"]
Action = Literal["deployments", "keys", "candidates"]


@pytest.mark.parametrize(
    "state", ["active", "membership_removed", "account_inactive", "tenant_suspended"]
)
@pytest.mark.parametrize("action", ["deployments", "keys", "candidates"])
async def test_control_lists_require_current_actor(
    db: AsyncSession, api_case: ApiCase, private_scopes: PrivateScopes, state: State, action: Action
) -> None:
    own = private_scopes.first
    match state:
        case "membership_removed":
            await db.execute(
                update(OrganizationMember)
                .where(
                    OrganizationMember.user_id == own.user_id,
                    OrganizationMember.org_id == own.org_id,
                )
                .values(status="removed")
                .execution_options(synchronize_session=False)
            )
            persisted = await db.scalar(
                select(OrganizationMember.status).where(
                    OrganizationMember.user_id == own.user_id,
                    OrganizationMember.org_id == own.org_id,
                )
            )
            assert persisted == "removed"
        case "account_inactive":
            await db.execute(
                update(User)
                .where(User.id == own.user_id)
                .values(is_active=False)
                .execution_options(synchronize_session=False)
            )
            assert await db.scalar(select(User.is_active).where(User.id == own.user_id)) is False
        case "tenant_suspended":
            await db.execute(
                update(Tenant)
                .where(Tenant.id == own.tenant_id)
                .values(status="suspended")
                .execution_options(synchronize_session=False)
            )
            assert (
                await db.scalar(select(Tenant.status).where(Tenant.id == own.tenant_id))
                == "suspended"
            )
        case "active":
            pass
    await db.commit()
    disclosed = False
    try:
        match action:
            case "deployments":
                rows = await service.list_deployments(db, own)
                disclosed = any(row.id == api_case.first.id for row in rows)
            case "keys":
                keys = await service.list_api_keys(db, own)
                disclosed = any(row.id == api_case.key.id for row in keys)
            case "candidates":
                candidates = await service.list_deployment_candidates(db, own)
                disclosed = any(row.agent_id == api_case.first.agent_id for row in candidates)
    except AppError as error:
        assert error.status in {403, 404}
    assert disclosed == (state == "active")
