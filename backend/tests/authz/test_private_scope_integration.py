"""Exact-20 native ORM/query boundary, separate from service/HTTP acceptance."""

from dataclasses import replace

import pytest
from sqlalchemy import select

from app.authz.org_context import resolve_org_context
from app.authz.platform_scope import PlatformScope
from app.authz.private_actor import PersistedActor
from app.authz.scoping import private_scoped, resource_candidates
from app.config import settings
from app.credentials import service as credentials
from app.credentials.creation import CredentialCreation
from app.migrations.org_authz_v1.contracts import M81_SCOPED
from app.models.credential import Credential
from app.models.model import Model
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.models.user import User
from tests.authz.native_private_http_checks import (
    assert_operator_http_boundaries,
    assert_owner_http_boundaries,
)
from tests.authz.native_private_mutation_checks import (
    MutationProbes,
    assert_foreign_mutations_denied,
    capture_rows,
)
from tests.authz.native_private_roots import seed_private_roots
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


async def test_exact_twenty_native_private_roots_are_disjoint_across_companies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    async with signup_app(monkeypatch) as stack:
        # Given actual JWT registrations and two companies for the same resource owner.
        for email in ("native-matrix@example.com", "native-operator@example.com"):
            registered = await stack.client.post(
                "/api/auth/register",
                json={
                    "email": email,
                    "name": "Matrix fixture",
                    "password": "native-matrix-password",
                },
            )
            assert registered.status_code == 201, registered.text
        async with stack.sessions() as db:
            owner = await db.scalar(select(User).where(User.email == "native-matrix@example.com"))
            operator = await db.scalar(
                select(User).where(User.email == "native-operator@example.com")
            )
            assert (
                owner is not None and operator is not None and owner.last_active_org_id is not None
            )
            first = await resolve_org_context(
                db,
                PersistedActor(owner.id, owner.is_super_user),
                cookie=str(owner.last_active_org_id),
            )
            company = Tenant(
                name="Other matrix company", slug="other-matrix", secrets_namespace="matrix"
            )
            db.add(company)
            await db.flush()
            organization = Organization(
                tenant_id=company.id, name="Other matrix org", slug="other-matrix"
            )
            db.add(organization)
            await db.flush()
            db.add_all(
                [
                    TenantMember(tenant_id=company.id, user_id=owner.id),
                    OrganizationMember(
                        org_id=organization.id, tenant_id=company.id, user_id=owner.id
                    ),
                ]
            )
            operator.is_super_user = True
            model = Model(provider="openai", model_name="fixture", display_name="Fixture")
            db.add(model)
            await db.flush()
            second = await resolve_org_context(
                db, PersistedActor(owner.id, owner.is_super_user), cookie=str(organization.id)
            )
            first_rows = await seed_private_roots(db, first, model)
            second_rows = await seed_private_roots(db, second, model)
            await db.commit()
            operator_context = await resolve_org_context(
                db, PersistedActor(operator.id, True), cookie=str(first.org_id)
            )
            assert len(first_rows.rows) == len(second_rows.rows) == len(M81_SCOPED) == 20
            assert {row.__tablename__ for row in first_rows.rows} == set(M81_SCOPED)
            # When every real mapped table is queried through the mandatory private boundary.
            for left, right in zip(first_rows.rows, second_rows.rows, strict=True):
                entity = type(left)
                assert entity is type(right)
                for row, context in ((left, first), (right, second)):
                    await db.refresh(row)
                    assert (row.user_id, row.org_id, row.tenant_id) == (
                        context.user_id,
                        context.org_id,
                        context.tenant_id,
                    ), row.__tablename__
                    ids = list(await db.scalars(private_scoped(select(entity.id), entity, context)))
                    assert ids == [row.id], row.__tablename__
                foreign = await db.scalar(
                    private_scoped(select(entity.id).where(entity.id == left.id), entity, second)
                )
                mismatch = replace(first, tenant_id=second.tenant_id)
                invalid_pair = await db.scalar(private_scoped(select(entity.id), entity, mismatch))
                operator_rows = list(
                    await db.scalars(private_scoped(select(entity.id), entity, operator_context))
                )
                # Then foreign IDs, invalid pairs and ungranted operator reads are absent.
                assert foreign is None and invalid_pair is None and operator_rows == [], (
                    entity.__tablename__
                )
            platform = await credentials.create(
                db,
                CredentialCreation(
                    "openai", "Platform matrix fixture", {"api_key": "dummy-platform"}
                ),
                PlatformScope(),
            )
            await db.flush()
            assert (
                platform.user_id is None and platform.org_id is None and platform.tenant_id is None
            )
            private_ids = set(
                await db.scalars(private_scoped(select(Credential.id), Credential, first))
            )
            candidate_ids = set(
                await db.scalars(resource_candidates(select(Credential.id), Credential, first))
            )
            assert platform.id not in private_ids
            assert candidate_ids == private_ids | {platform.id}
            await db.commit()
            original_rows = await capture_rows(db, first_rows)
        # Production HTTP guards are exercised separately from the generic ORM queries.
        login = await stack.client.post(
            "/api/auth/login",
            json={"email": "native-matrix@example.com", "password": "native-matrix-password"},
        )
        assert login.status_code == 200, login.text
        await assert_owner_http_boundaries(stack.client, first_rows, second_rows)
        await assert_foreign_mutations_denied(
            stack.client, MutationProbes(first_rows, login.json()["csrf_token"])
        )
        operator_login = await stack.client.post(
            "/api/auth/login",
            json={"email": "native-operator@example.com", "password": "native-matrix-password"},
        )
        assert operator_login.status_code == 200, operator_login.text
        await assert_operator_http_boundaries(stack.client, first_rows)
        await assert_foreign_mutations_denied(
            stack.client, MutationProbes(first_rows, operator_login.json()["csrf_token"])
        )
        async with stack.sessions() as db:
            assert await capture_rows(db, first_rows) == original_rows
