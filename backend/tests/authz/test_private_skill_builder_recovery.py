"""Post-claim recovery changes only the originally authorized private session tuple."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.models.skill_builder_session import SkillBuilderSession
from app.schemas.skill_builder import SkillBuilderStatus
from app.services import skill_builder_finalize, skill_builder_service
from tests.conftest import TestSession

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(frozen=True, slots=True)
class RecoveryScenario:
    session_id: UUID
    context: OrgContext
    stored_org: UUID
    stored_tenant: UUID
    can_release: bool


@pytest.fixture(params=["own", "foreign", "moved"])
async def recovery_scenario(
    db: AsyncSession, private_scopes: PrivateScopes, request: pytest.FixtureRequest
) -> RecoveryScenario:
    row = await skill_builder_service.create_session(
        db, context=private_scopes.first, user_request="Recovery fixture"
    )
    row.status = SkillBuilderStatus.CONFIRMING.value
    context = private_scopes.first
    if request.param == "foreign":
        context = private_scopes.second
    elif request.param == "moved":
        row.org_id = private_scopes.second.org_id
        row.tenant_id = private_scopes.second.tenant_id
    await db.commit()
    return RecoveryScenario(row.id, context, row.org_id, row.tenant_id, request.param == "own")


async def test_claim_recovery_keeps_exact_original_scope(
    db: AsyncSession, recovery_scenario: RecoveryScenario
) -> None:
    # Given a committed claim and a matching, foreign or stale persisted target scope.
    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(skill_builder_finalize, "async_session", TestSession)
        # When recovery opens its independent transaction after caller rollback.
        await skill_builder_finalize._release_confirming_claim(
            db, recovery_scenario.session_id, recovery_scenario.context
        )
    db.expunge_all()
    row = await db.get(SkillBuilderSession, recovery_scenario.session_id)
    # Then metadata cleanup never resets a same-owner session in another company.
    assert row is not None
    assert row.status == (
        SkillBuilderStatus.REVIEW.value
        if recovery_scenario.can_release
        else SkillBuilderStatus.CONFIRMING.value
    )
    assert (row.org_id, row.tenant_id) == (
        recovery_scenario.stored_org,
        recovery_scenario.stored_tenant,
    )
