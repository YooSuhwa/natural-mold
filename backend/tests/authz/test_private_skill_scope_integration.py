"""Real m83 Skill and SkillBuilderSession writers preserve request scope on PostgreSQL."""

from pathlib import Path
from typing import Literal, assert_never
from uuid import UUID

import pytest
from sqlalchemy import select

from app.authz.org_context import resolve_org_context
from app.authz.private_actor import PersistedActor
from app.config import settings
from app.models.skill import Skill
from app.models.skill_builder_session import SkillBuilderSession
from app.models.user import User
from app.services import skill_builder_finalize, skill_builder_service
from tests.authz.native_company_selection import select_second_company
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("kind", ["own", "foreign", "moved"])
async def test_native_skill_builder_recovery_keeps_exact_claim_scope(
    monkeypatch: pytest.MonkeyPatch, kind: Literal["own", "foreign", "moved"]
) -> None:
    # Given an independently migrated database and a committed private builder claim.
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-recovery@example.com",
                "name": "Recovery fixture",
                "password": "native-recovery-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        async with stack.sessions() as db:
            user = await db.scalar(select(User).where(User.email == "native-recovery@example.com"))
            assert user is not None and user.last_active_org_id is not None
            original = await resolve_org_context(
                db, PersistedActor(user.id, user.is_super_user), cookie=str(user.last_active_org_id)
            )
            row = await skill_builder_service.create_session(
                db, context=original, user_request="Recovery fixture"
            )
            row.status = "confirming"
            await db.commit()
            session_id = row.id
        other = await select_second_company(stack, headers, "native-recovery@example.com")
        context = original
        match kind:
            case "foreign":
                context = other
            case "moved":
                async with stack.sessions() as db:
                    row = await db.get(SkillBuilderSession, session_id)
                    assert row is not None
                    row.org_id, row.tenant_id = other.org_id, other.tenant_id
                    await db.commit()
            case "own":
                pass
            case unreachable:
                assert_never(unreachable)
        monkeypatch.setattr(skill_builder_finalize, "async_session", stack.sessions)
        # When cleanup uses its own transaction with the originally bound scope.
        async with stack.sessions() as db:
            await skill_builder_finalize._release_confirming_claim(db, session_id, context)
        # Then preference switching preserves own recovery, while foreign/stale tuples do not write.
        async with stack.sessions() as db:
            row = await db.get(SkillBuilderSession, session_id)
            assert row is not None
            assert row.status == ("review" if kind == "own" else "confirming")
            expected = other if kind == "moved" else original
            assert (row.user_id, row.org_id, row.tenant_id) == (
                expected.user_id,
                expected.org_id,
                expected.tenant_id,
            )


@pytest.mark.parametrize("other_company", [False, True])
async def test_native_m83_skill_and_builder_session_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, other_company: bool
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    async with signup_app(monkeypatch) as stack:
        registered = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-skill-scope@example.com",
                "name": "Skill fixture",
                "password": "native-skill-password",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"X-CSRF-Token": registered.json()["csrf_token"]}
        if other_company:
            await select_second_company(stack, headers, "native-skill-scope@example.com")
        created = await stack.client.post(
            "/api/skills",
            headers={"X-CSRF-Token": registered.json()["csrf_token"]},
            json={
                "name": "Native fixture",
                "slug": "native-fixture",
                "content": (
                    '---\nname: native-fixture\ndescription: "Native scope fixture"\n---\n'
                    "\nFixture body\n"
                ),
            },
        )
        assert created.status_code == 201, created.text
        async with stack.sessions() as db:
            user = await db.scalar(
                select(User).where(User.email == "native-skill-scope@example.com")
            )
            skill = await db.get(Skill, UUID(created.json()["id"]))
            assert user is not None and skill is not None and user.last_active_org_id is not None
            context = await resolve_org_context(
                db, PersistedActor(user.id, user.is_super_user), cookie=str(user.last_active_org_id)
            )
            assert (skill.user_id, skill.org_id, skill.tenant_id) == (
                context.user_id,
                context.org_id,
                context.tenant_id,
            )
            builder = await skill_builder_service.create_session(
                db, context=context, user_request="Native builder fixture"
            )
            await db.commit()
            builder_id = builder.id
        async with stack.sessions() as db:
            persisted = await db.get(SkillBuilderSession, builder_id)
            assert persisted is not None
            assert (persisted.user_id, persisted.org_id, persisted.tenant_id) == (
                context.user_id,
                context.org_id,
                context.tenant_id,
            )
