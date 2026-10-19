"""I-06: request paths without an org context bind the right scope.

aiosqlite never evaluates the policies, so these tests assert the binding the
session carries (``session.info``), which PostgreSQL turns into
``set_config(..., true)`` on every transaction (``test_i06_identity_scope``).
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from starlette.requests import Request

from app.agent_api.dependencies import get_api_key_principal
from app.agent_api.security import generate_api_key
from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.database import (
    REASON_INFO_KEY,
    SCOPE_INFO_KEY,
    SessionScopeError,
    SessionTenant,
    bind_platform,
    platform_scope,
    session_tenant,
    tenant_session,
)
from app.dependencies import get_company_db, require_platform_operator
from app.exceptions import AppError
from app.models.agent_api import AgentApiKey
from app.models.conversation import Conversation
from app.models.share_link import ShareLink
from app.services.share_rate_scope import public_share_rate_scope
from app.tenancy.context import resolve_tenant_context
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.org_context_fixture import OrgFixture
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


def _request(*, cookie: str | None = None, authorization: str | None = None) -> Request:
    headers = []
    if cookie is not None:
        headers.append((b"cookie", f"{ACTIVE_ORG_COOKIE}={cookie}".encode()))
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def _scope(db: object) -> dict[str, str] | None:
    return db.info.get(SCOPE_INFO_KEY)  # type: ignore[attr-defined]


async def test_route_without_org_context_binds_the_active_company(
    org_fixture: OrgFixture, private_scopes: PrivateScopes
) -> None:
    second = private_scopes.second
    async with TestSession() as db:
        assert await get_company_db(_request(cookie=str(second.org_id)), org_fixture.user, db) is db
        assert _scope(db) == {
            "app.tenant_id": str(second.tenant_id),
            "app.user_id": str(second.user_id),
        }
    async with TestSession() as db:  # a foreign organization never fails the route
        await get_company_db(_request(cookie=str(org_fixture.foreign)), org_fixture.user, db)
        assert _scope(db) is None


async def test_path_company_is_bound_after_membership_and_bound_sessions_are_kept(
    org_fixture: OrgFixture, private_scopes: PrivateScopes
) -> None:
    first, second = private_scopes.first, private_scopes.second
    async with TestSession() as db:
        context = await resolve_tenant_context(db, org_fixture.user, second.tenant_id)
        assert session_tenant(db) == context.tenant_id == second.tenant_id
    async with TestSession() as db:
        with pytest.raises(AppError):
            await resolve_tenant_context(db, org_fixture.user, uuid.uuid4())
        assert _scope(db) is None  # a denied company never binds
    async with tenant_session(first, maker=TestSession) as db:
        await resolve_tenant_context(db, org_fixture.user, second.tenant_id)
        assert session_tenant(db) == first.tenant_id  # legacy-identical: no re-binding


async def test_api_key_binds_the_key_company_before_the_secret_check(
    org_fixture: OrgFixture, private_scopes: PrivateScopes
) -> None:
    second = private_scopes.second
    generated = generate_api_key()
    async with TestSession() as db:
        db.add(
            AgentApiKey(
                user_id=second.user_id,
                org_id=second.org_id,
                tenant_id=second.tenant_id,
                name="i06",
                key_id=generated.key_id,
                key_hash=generated.secret_hash,
                prefix=generated.prefix,
                last_four=generated.last_four,
                scopes=[],
            )
        )
        await db.commit()
    async with TestSession() as db:
        principal = await get_api_key_principal(
            _request(authorization=f"Bearer {generated.cleartext}"), db
        )
        assert principal.context.tenant_id == second.tenant_id
        assert _scope(db) == {"app.tenant_id": str(second.tenant_id)}  # company only
    wrong = f"moldy_sk_{generated.key_id}_{'x' * 32}"
    async with TestSession() as db:  # a wrong secret never lends the owner's identity
        with pytest.raises(AppError):
            await get_api_key_principal(_request(authorization=f"Bearer {wrong}"), db)
        assert _scope(db) == {"app.tenant_id": str(second.tenant_id)}
    unknown = generate_api_key()
    async with TestSession() as db:
        with pytest.raises(AppError):
            await get_api_key_principal(_request(authorization=f"Bearer {unknown.cleartext}"), db)
        assert _scope(db) is None


async def test_public_share_token_binds_the_link_company(
    org_fixture: OrgFixture, private_scopes: PrivateScopes
) -> None:
    second = private_scopes.second
    async with TestSession() as db:
        conversation = Conversation(
            user_id=second.user_id,
            org_id=second.org_id,
            tenant_id=second.tenant_id,
            title="s",
            agent_id=uuid.uuid4(),
            agent_name_snapshot="a",
            last_activity_at=datetime.now(UTC),
        )
        db.add(conversation)
        await db.flush()
        db.add(
            ShareLink(
                share_token="i06-token",
                conversation_id=conversation.id,
                created_by=uuid.uuid4(),  # aiosqlite does not enforce the FK
                org_id=second.org_id,
                tenant_id=second.tenant_id,
            )
        )
        await db.commit()
    request = _request()
    async with TestSession() as db:
        await public_share_rate_scope(request, "i06-token", db)
        assert request.state.share_tenant_id == second.tenant_id
        # the conversation owner (the identity the public reads resolve), not created_by
        assert _scope(db) == {
            "app.tenant_id": str(second.tenant_id),
            "app.user_id": str(second.user_id),
        }
    async with TestSession() as db:
        await public_share_rate_scope(request, "unknown", db)
        assert request.state.share_tenant_id is None and _scope(db) is None


async def test_platform_scope_is_temporary_and_bind_platform_is_exclusive(
    org_fixture: OrgFixture,
) -> None:
    async with TestSession() as db:
        async with platform_scope(db, "share.public_token"):
            assert _scope(db) == {"app.scope": "platform"}
        assert _scope(db) is None
        operator = replace(org_fixture.user, is_super_user=True)
        await require_platform_operator(operator, db)
        assert _scope(db) == {"app.scope": "platform"}
        assert db.info[REASON_INFO_KEY] == "operator.system_catalog"
    async with tenant_session(SessionTenant(uuid.uuid4()), maker=TestSession) as db:
        async with platform_scope(db, "share.public_token"):  # a bound session is left alone
            assert "app.scope" not in (_scope(db) or {})
        with pytest.raises(SessionScopeError):
            await bind_platform(db, "operator.system_catalog")
