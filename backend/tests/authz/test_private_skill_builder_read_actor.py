"""Captured private readers refresh authority before metadata or SQL effects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.exceptions import AppError
from app.models.organization import OrganizationMember
from app.services import skill_builder_service

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_agent_api_scope import ApiCase
pytest_plugins = ["tests.authz.test_private_agent_api_scope"]


@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize("action", ["detail", "list", "fresh_http"])
async def test_skill_builder_reads_authorize_actor(
    db: AsyncSession,
    api_case: ApiCase,
    private_scopes: PrivateScopes,
    action: Literal["detail", "list", "fresh_http"],
    revoked: bool,
) -> None:
    own = private_scopes.first
    session = await skill_builder_service.create_session(
        db, context=own, user_request="Independent actor metadata witness"
    )
    await db.commit()
    if revoked:
        await db.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.user_id == own.user_id, OrganizationMember.org_id == own.org_id
            )
            .values(status="removed")
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        assert (
            await db.scalar(
                select(OrganizationMember.status).where(
                    OrganizationMember.user_id == own.user_id,
                    OrganizationMember.org_id == own.org_id,
                )
            )
            == "removed"
        )
    disclosed = False
    try:
        match action:
            case "detail":
                row = await skill_builder_service.get_session(db, session.id, own)
                disclosed = row is not None and row.id == session.id
            case "list":
                rows = await skill_builder_service.list_sessions(db, context=own)
                disclosed = any(row.id == session.id for row in rows)
            case "fresh_http":
                api_case.client.cookies.set(ACTIVE_ORG_COOKIE, str(own.org_id))
                response = await api_case.client.get("/api/skill-builder")
                if revoked:
                    assert response.status_code == 403
                else:
                    assert response.status_code == 200
                disclosed = response.status_code == 200 and any(
                    row["id"] == str(session.id) for row in response.json()
                )
    except AppError as error:
        assert error.status in {403, 404}
    assert disclosed != revoked
