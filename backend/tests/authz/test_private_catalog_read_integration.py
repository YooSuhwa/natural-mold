"""Actual m83 read consumers reject captured authority revoked by another session."""

from typing import Literal, assert_never
from uuid import UUID

import pytest
from sqlalchemy import select, update

from app.authz.blueprint_read_scope import read_blueprint_presentations
from app.authz.org_context import resolve_org_context
from app.config import settings
from app.dependencies import CurrentUser
from app.exceptions import AppError
from app.marketplace.service import get_item
from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import MarketplaceItem
from app.models.organization import OrganizationMember
from app.models.tool import Tool
from app.models.user import User
from tests.authz.catalog_actor_fixture import catalog_sql
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration

type NativeRead = Literal["catalog", "blueprint"]


@pytest.mark.parametrize(
    "case", [(action, revoked) for action in ("catalog", "blueprint") for revoked in (False, True)]
)
async def test_native_read_rechecks_actor_without_flushing_pending_tool(
    monkeypatch: pytest.MonkeyPatch, case: tuple[NativeRead, bool]
) -> None:
    action, revoked = case
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-read@example.com",
                "name": "Read fixture",
                "password": "native-read-fixture-password",
            },
        )
        assert registered.status_code == 201, registered.text
        user_id = UUID(registered.json()["user"]["id"])
        async with stack.sessions() as db:
            user = await db.get(User, user_id)
            assert user is not None
            actor = CurrentUser(id=user.id, email=user.email, name=user.name)
            context = await resolve_org_context(db, actor, cookie=None)
            source = MarketplaceItem(
                owner_user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                resource_type="agent",
                name="Native private catalog",
                slug="native-private-catalog",
                visibility="private",
                status="published",
                is_system=False,
            )
            blueprint = AgentBlueprint(
                user_id=context.user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                name="Native private Blueprint",
                spec={"system_prompt": "native-private-fixture"},
                spec_hash="f" * 64,
            )
            db.add_all([source, blueprint])
            await db.commit()
            await get_item(db, user=actor, context=context, item_id=source.id)
            if revoked:
                async with stack.sessions() as writer:
                    await writer.execute(
                        update(OrganizationMember)
                        .where(
                            OrganizationMember.user_id == user_id,
                            OrganizationMember.org_id == context.org_id,
                        )
                        .values(status="removed")
                    )
                    await writer.commit()
            pending = Tool(
                user_id=user_id,
                org_id=context.org_id,
                tenant_id=context.tenant_id,
                name="Pending native read tool",
                definition_key="http_request",
            )
            db.add(pending)
            consumed = False
            with catalog_sql(db) as statements:
                try:
                    match action:
                        case "catalog":
                            current = await get_item(
                                db, user=actor, context=context, item_id=source.id
                            )
                            consumed = (
                                current is not None and current.name == "Native private catalog"
                            )
                        case "blueprint":
                            rows = await read_blueprint_presentations(db, context, blueprint.id)
                            consumed = len(rows) == 1 and rows[0].spec == {
                                "system_prompt": "native-private-fixture"
                            }
                        case unreachable:
                            assert_never(unreachable)
                except AppError as error:
                    assert error.status in {403, 404}
            assert consumed is not revoked
            if revoked:
                assert not any(
                    sql.lower().lstrip().startswith(("insert ", "update ", "delete "))
                    for sql in statements
                )
                assert pending in db.new
            else:
                await db.flush()
                assert await db.scalar(select(Tool.id).where(Tool.id == pending.id)) == pending.id
