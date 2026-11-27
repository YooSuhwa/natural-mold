"""A detached AG-UI reader uses the captured organization and current permissions on every poll."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from app.auth.jwt import create_access_token
from app.authz.org_context import OrgContext
from app.config import settings
from app.database import ShieldedAsyncSession, tenant_session
from app.exceptions import AppError
from app.models import Conversation, Organization, OrganizationMember, TenantMember, User
from app.services.ag_ui_read_access import require_read_conversation
from tests.authz.i06_rls_lab import async_url, rls_lab

pytestmark = pytest.mark.integration


def _history(db: Connection, context: OrgContext) -> UUID:
    scope = {"org_id": context.org_id, "tenant_id": context.tenant_id, "user_id": context.user_id}
    db.execute(
        sa.insert(Organization).values(
            id=context.org_id, tenant_id=context.tenant_id, name="Reader", slug=context.org_id.hex
        )
    )
    db.execute(sa.insert(OrganizationMember).values(**scope))
    conversation_id = uuid4()
    now = datetime.now(UTC)
    db.execute(
        sa.insert(Conversation).values(
            **scope,
            id=conversation_id,
            agent_name_snapshot="Archived agent",
            agent_lifecycle_state="purged",
            agent_purged_at=now,
            last_activity_at=now,
        )
    )
    return conversation_id


def _request(token: str) -> Request:
    return Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})


async def test_polls_use_captured_scope_and_observe_committed_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "db_rls_enabled", True)
    monkeypatch.setattr(settings, "jwt_secret", uuid4().hex + uuid4().hex)
    with rls_lab() as lab:
        owner, peer = uuid4(), uuid4()
        first = OrgContext(org_id=uuid4(), tenant_id=lab.tenant_a, user_id=owner)
        second = OrgContext(org_id=uuid4(), tenant_id=lab.tenant_a, user_id=owner)
        foreign = OrgContext(org_id=uuid4(), tenant_id=lab.tenant_b, user_id=owner)
        with lab.admin.begin() as admin:
            for user_id in (owner, peer):
                admin.execute(
                    sa.insert(User).values(
                        id=user_id, email=f"{user_id.hex}@read.test", name="User"
                    )
                )
                admin.execute(
                    sa.insert(TenantMember).values(tenant_id=lab.tenant_a, user_id=user_id)
                )
            admin.execute(sa.insert(TenantMember).values(tenant_id=lab.tenant_b, user_id=owner))
            first_chat = _history(admin, first)
            second_chat = _history(admin, second)
            foreign_chat = _history(admin, foreign)
            admin.execute(
                sa.insert(OrganizationMember).values(
                    org_id=first.org_id, tenant_id=first.tenant_id, user_id=peer
                )
            )
            admin.execute(
                sa.update(User).where(User.id == owner).values(last_active_org_id=first.org_id)
            )
        engine = create_async_engine(
            async_url(lab.app_url),
            connect_args={"server_settings": {"default_transaction_read_only": "on"}},
        )
        sessions = async_sessionmaker(engine, class_=ShieldedAsyncSession, expire_on_commit=False)
        request = _request(create_access_token(owner))

        async def poll(
            context: OrgContext, conversation_id: UUID, incoming: Request = request
        ) -> UUID:
            async with tenant_session(context, maker=sessions) as db:
                row = await require_read_conversation(
                    db, request=incoming, context=context, conversation_id=conversation_id
                )
                return row.id

        try:
            assert await poll(first, first_chat) == first_chat
            with lab.admin.begin() as admin:
                admin.execute(
                    sa.update(User).where(User.id == owner).values(last_active_org_id=second.org_id)
                )
            assert await poll(first, first_chat) == first_chat
            assert await poll(second, second_chat) == second_chat
            assert await poll(foreign, foreign_chat) == foreign_chat
            for hidden in (uuid4(), second_chat, foreign_chat):
                with pytest.raises(AppError) as denied:
                    await poll(first, hidden)
                assert (denied.value.status, denied.value.code) == (404, "CONVERSATION_NOT_FOUND")
            with pytest.raises(AppError) as wrong_actor:
                await poll(first, first_chat, _request(create_access_token(peer)))
            assert wrong_actor.value.status == 404
            with pytest.raises(AppError) as invalid_token:
                await poll(first, first_chat, _request("invalid"))
            assert invalid_token.value.status == 401

            with lab.admin.begin() as admin:
                admin.execute(
                    sa.update(OrganizationMember)
                    .where(
                        OrganizationMember.org_id == first.org_id,
                        OrganizationMember.user_id == owner,
                    )
                    .values(status="removed")
                )
            with pytest.raises(AppError) as revoked:
                await poll(first, first_chat)
            assert (revoked.value.status, revoked.value.code) == (404, "CONVERSATION_NOT_FOUND")
            assert await poll(second, second_chat) == second_chat

            with lab.admin.begin() as admin:
                admin.execute(
                    sa.update(TenantMember)
                    .where(TenantMember.tenant_id == lab.tenant_b, TenantMember.user_id == owner)
                    .values(status="suspended")
                )
            with pytest.raises(AppError) as suspended:
                await poll(foreign, foreign_chat)
            assert (suspended.value.status, suspended.value.code) == (404, "CONVERSATION_NOT_FOUND")
            assert await poll(second, second_chat) == second_chat

            with lab.admin.begin() as admin:
                admin.execute(sa.update(User).where(User.id == owner).values(is_active=False))
            with pytest.raises(AppError) as inactive:
                await poll(second, second_chat)
            assert inactive.value.status == 401
        finally:
            await engine.dispose()
