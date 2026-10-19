"""Real m83 transactions deduplicate installs per company under row-lock contention."""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import anyio
import pytest
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.authz.org_context import OrgContext
from app.config import settings
from app.marketplace import install_service
from app.models.agent_blueprint import AgentBlueprint
from app.models.marketplace import MarketplaceInstallation, MarketplaceItem
from app.models.model import Model
from app.models.organization import Organization
from app.models.user import User
from tests.authz.native_company_selection import select_second_company
from tests.authz.test_registration_policy_integration import SignupApp, signup_app

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class InstallReply:
    context: OrgContext
    response: Response


def _cookie_header(stack: SignupApp, context: OrgContext) -> str:
    parts = [
        f"{name}={value}"
        for name, value in stack.client.cookies.items()
        if name != ACTIVE_ORG_COOKIE
    ]
    parts.append(f"{ACTIVE_ORG_COOKIE}={context.org_id}")
    return "; ".join(parts)


async def test_real_concurrent_installers_reuse_only_their_exact_company(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-concurrent-install@example.com",
                "name": "Concurrent install",
                "password": "native-concurrent-install-password",
            },
        )
        assert registered.status_code == 201, registered.text
        csrf = {"X-CSRF-Token": registered.json()["csrf_token"]}
        async with stack.sessions() as db:
            user = await db.get(User, UUID(registered.json()["user"]["id"]))
            assert user is not None and user.last_active_org_id is not None
            organization = await db.get(Organization, user.last_active_org_id)
            assert organization is not None
            first = OrgContext(
                user_id=user.id, org_id=organization.id, tenant_id=organization.tenant_id
            )
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.commit()
            model_id = model.id
        created = await stack.client.post(
            "/api/agents",
            headers=csrf,
            json={
                "name": "Concurrent install template",
                "system_prompt": "Fixture",
                "model_id": str(model_id),
            },
        )
        assert created.status_code == 201, created.text
        published = await stack.client.post(
            f"/api/marketplace/items/from-agent/{created.json()['id']}",
            headers=csrf,
            json={"name": "Concurrent install template", "visibility": "unlisted"},
        )
        assert published.status_code == 201, published.text
        item_id = UUID(published.json()["id"])
        second = await select_second_company(stack, csrf, "native-concurrent-install@example.com")
        contexts = (first, first, second, second)
        replies: list[InstallReply] = []
        entered: list[UUID] = []
        all_waiting = anyio.Event()
        original_lock = install_service.lock_marketplace_item_install

        async def observe_real_lock(db: AsyncSession, *, item_id: UUID) -> None:
            entered.append(item_id)
            if len(entered) == len(contexts):
                all_waiting.set()
            await original_lock(db, item_id=item_id)

        monkeypatch.setattr(install_service, "lock_marketplace_item_install", observe_real_lock)

        async def install(context: OrgContext) -> None:
            response = await stack.client.post(
                f"/api/marketplace/items/{item_id}/install",
                json={},
                headers={**csrf, "Cookie": _cookie_header(stack, context)},
            )
            replies.append(InstallReply(context, response))

        # Hold the actual PG lock until all four HTTP requests reach their real
        # lock seam. The observer does not replace locking or inject existing rows.
        async with stack.sessions() as holder:
            await holder.scalar(
                select(MarketplaceItem).where(MarketplaceItem.id == item_id).with_for_update()
            )
            async with anyio.create_task_group() as tasks:
                for context in contexts:
                    tasks.start_soon(install, context)
                with anyio.fail_after(20):
                    await all_waiting.wait()
                await holder.commit()
        assert len(replies) == len(entered) == 4
        assert all(reply.response.status_code == 201 for reply in replies), [
            reply.response.text for reply in replies if reply.response.status_code != 201
        ]
        ids_by_org = {
            context.org_id: {
                reply.response.json()["id"] for reply in replies if reply.context == context
            }
            for context in (first, second)
        }
        assert all(len(ids) == 1 for ids in ids_by_org.values())
        assert ids_by_org[first.org_id].isdisjoint(ids_by_org[second.org_id])
        async with stack.sessions() as db:
            installations = list(
                (
                    await db.scalars(
                        select(MarketplaceInstallation).where(
                            MarketplaceInstallation.item_id == item_id
                        )
                    )
                ).all()
            )
            assert len(installations) == 2
            blueprints = list((await db.scalars(select(AgentBlueprint))).all())
            assert len(blueprints) == 2
            for context in (first, second):
                installation = next(row for row in installations if row.org_id == context.org_id)
                child = next(
                    row for row in blueprints if row.id == installation.installed_agent_blueprint_id
                )
                assert (installation.user_id, installation.org_id, installation.tenant_id) == (
                    context.user_id,
                    context.org_id,
                    context.tenant_id,
                )
                assert (child.user_id, child.org_id, child.tenant_id) == (
                    context.user_id,
                    context.org_id,
                    context.tenant_id,
                )
