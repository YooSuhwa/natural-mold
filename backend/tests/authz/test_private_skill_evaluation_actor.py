"""Evaluation reauthorizes its persisted private Skill before mounting or executing it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.credentials import service as credential_service
from app.credentials.creation import CredentialCreation
from app.marketplace.credential_requirements import resolve_credential_bindings
from app.models.organization import OrganizationMember, Tenant
from app.models.skill_evaluation import SkillEvaluationRun
from app.models.user import User
from app.services import skill_evaluation_service
from app.services.skill_evaluation_worker import SkillEvaluationWorker
from app.services.skill_evaluation_worker_state import build_context
from app.services.skill_evaluation_worker_types import SkillEvaluationContext, SkillEvaluationResult

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes
    from tests.authz.test_private_skill_scope import SkillCase

pytest_plugins = ["tests.authz.test_private_skill_scope"]


class NeverEvaluate:
    async def evaluate(
        self, db: AsyncSession, context: SkillEvaluationContext
    ) -> SkillEvaluationResult:
        raise AssertionError("Revoked authority must fail before evaluation")


@pytest.fixture
async def private_evaluation(db: AsyncSession, skill_case: SkillCase) -> SkillEvaluationRun:
    skill = skill_case.first
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db, user_id=skill.user_id, skill=skill, name="Actor fixture", evals=[{"input": "Fixture"}]
    )
    run = await skill_evaluation_service.create_run(
        db, user_id=skill.user_id, skill=skill, evaluation_set=evaluation_set
    )
    await db.commit()
    return run


@pytest.mark.parametrize("change", ["membership", "company", "account"])
async def test_evaluation_terminalizes_revoked_actor_before_runner(
    db: AsyncSession,
    private_evaluation: SkillEvaluationRun,
    change: Literal["membership", "company", "account"],
) -> None:
    from app.models.skill import Skill

    skill = await db.get(Skill, private_evaluation.skill_id)
    assert skill is not None
    match change:
        case "membership":
            await db.execute(
                delete(OrganizationMember).where(
                    OrganizationMember.org_id == skill.org_id,
                    OrganizationMember.user_id == skill.user_id,
                )
            )
        case "company":
            await db.execute(
                update(Tenant).where(Tenant.id == skill.tenant_id).values(status="suspended")
            )
        case "account":
            await db.execute(update(User).where(User.id == skill.user_id).values(is_active=False))
    await db.commit()
    worker = SkillEvaluationWorker(evaluator=NeverEvaluate())
    completed = await worker.run_once(db, private_evaluation.id)
    assert completed is not None and completed.status == "failed"
    assert completed.error_message is not None
    assert {"membership": "forbidden", "company": "tenant_suspended", "account": "not_found"}[
        change
    ] in completed.error_message


async def test_evaluation_keeps_target_org_after_saved_company_switch(
    db: AsyncSession, private_evaluation: SkillEvaluationRun, private_scopes: PrivateScopes
) -> None:
    await db.execute(
        update(User)
        .where(User.id == private_scopes.first.user_id)
        .values(last_active_org_id=private_scopes.second.org_id)
    )
    await db.commit()
    context = await build_context(db, private_evaluation)
    assert context.skill_id == private_evaluation.skill_id
    assert list(context.runtime_context.descriptors) == ["scoped-fixture"]


async def test_foreign_company_credential_is_missing_without_decryption(
    db: AsyncSession, skill_case: SkillCase, private_scopes: PrivateScopes
) -> None:
    foreign = await credential_service.create(
        db,
        CredentialCreation(
            definition_key="openai", name="Foreign fixture", data={"api_key": "dummy-foreign-only"}
        ),
        private_scopes.second,
    )
    skill_case.first.credential_requirements = [
        {
            "key": "api",
            "definition_key": "openai",
            "required": True,
            "injection": "env",
            "scope": "user",
            "env_map": {"api_key": "OPENAI_API_KEY"},
        }
    ]
    await db.commit()
    from unittest.mock import patch

    decrypt = AsyncMock(side_effect=AssertionError("Foreign credential must not be decrypted"))
    with patch.object(credential_service, "decrypt_with_external", decrypt):
        resolved, missing = await resolve_credential_bindings(
            db,
            skill=skill_case.first,
            context=private_scopes.first,
            agent_skill_config={"credential_bindings": {"api": str(foreign.id)}},
        )
    assert resolved == {} and missing == ["api"]
    decrypt.assert_not_awaited()
