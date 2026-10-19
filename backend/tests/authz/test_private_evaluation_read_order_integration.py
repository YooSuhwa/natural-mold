"""Actual m83 evaluation contexts authorize before private payload hydration."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext, resolve_org_context
from app.config import settings
from app.dependencies import CurrentUser
from app.models.organization import OrganizationMember, TenantMember
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.models.tool import Tool
from app.models.user import User
from app.services.skill_evaluation_worker_state import build_context, load_run
from app.services.skill_evaluation_worker_types import SkillEvaluationExecutionError
from tests.authz.queue_resource_fixture import queue_resource_sql
from tests.authz.test_registration_policy_integration import signup_app

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class Case:
    state: Literal["active", "org_removed", "tenant_removed", "inactive"]
    operator: bool


@dataclass(frozen=True, slots=True)
class Target:
    context: OrgContext
    run: SkillEvaluationRun
    skill_id: UUID


@pytest.fixture
def _stub_llm_credential_resolution() -> None:
    """Use real authority and credential resolution with a keyless fixture Skill."""


async def _seed(db: AsyncSession, context: OrgContext, storage_root: Path) -> Target:
    own = context
    storage = storage_root / "evaluation-skill"
    storage.mkdir()
    (storage / "SKILL.md").write_text(
        "---\nname: f2-evaluation\ndescription: fixture\n---\nPRIVATE_SKILL_INSTRUCTION"
    )
    skill = Skill(
        user_id=own.user_id,
        org_id=own.org_id,
        tenant_id=own.tenant_id,
        name="F2 private evaluation skill",
        slug="f2-evaluation",
        kind="text",
        storage_path=str(storage / "SKILL.md"),
        credential_requirements=[],
    )
    db.add(skill)
    await db.flush()
    cases = SkillEvaluationSet(
        user_id=own.user_id,
        skill_id=skill.id,
        name="F2",
        evals=[{"input": "PRIVATE_EVALUATION_INPUT"}],
    )
    db.add(cases)
    await db.flush()
    run = SkillEvaluationRun(
        user_id=own.user_id,
        skill_id=skill.id,
        evaluation_set_id=cases.id,
        skill_version="read-order-1",
        skill_content_hash="a" * 64,
        run_config={"baseline_comparison": False},
    )
    db.add(run)
    await db.commit()
    return Target(context, run, skill.id)


@pytest.mark.parametrize(
    "case",
    [
        Case(s, o)
        for s in ("active", "org_removed", "tenant_removed", "inactive")
        for o in (False, True)
    ],
)
async def test_native_evaluation_actor_precedes_private_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: Case
) -> None:
    monkeypatch.setattr(settings, "registration_policy", "default_org")
    monkeypatch.setattr(settings, "allow_first_user_as_admin", case.operator)
    async with signup_app(monkeypatch) as stack:
        response = await stack.client.post(
            "/api/auth/register",
            json={
                "email": "native-evaluation-order@example.com",
                "name": "Evaluator",
                "password": "native-evaluation-order-fixture-password",
            },
        )
        assert response.status_code == 201, response.text
        user_id = UUID(response.json()["user"]["id"])
        async with stack.sessions() as db:
            user = await db.get(User, user_id)
            assert user is not None and user.is_super_user is case.operator
            context = await resolve_org_context(
                db,
                CurrentUser(
                    id=user.id,
                    email=user.email,
                    name=user.name,
                    is_super_user=case.operator,
                ),
                cookie=None,
            )
            target = await _seed(db, context, tmp_path)
            own = target.context
            async with stack.sessions() as writer:
                await writer.execute(
                    update(User).where(User.id == own.user_id).values(is_super_user=case.operator)
                )
                match case.state:
                    case "org_removed":
                        await writer.execute(
                            update(OrganizationMember)
                            .where(
                                OrganizationMember.org_id == own.org_id,
                                OrganizationMember.user_id == own.user_id,
                            )
                            .values(status="removed")
                        )
                    case "tenant_removed":
                        await writer.execute(
                            update(TenantMember)
                            .where(
                                TenantMember.tenant_id == own.tenant_id,
                                TenantMember.user_id == own.user_id,
                            )
                            .values(status="removed")
                        )
                    case "inactive":
                        await writer.execute(
                            update(User).where(User.id == own.user_id).values(is_active=False)
                        )
                    case "active":
                        pass
                    case unreachable:
                        assert_never(unreachable)
                await writer.commit()
            pending_id = uuid4()
            pending = Tool(
                id=pending_id,
                user_id=own.user_id,
                org_id=own.org_id,
                tenant_id=own.tenant_id,
                name="Evaluation caller write",
                definition_key="http_request",
            )
            db.add(pending)
            denied = False
            with queue_resource_sql(db) as sql:
                try:
                    run = await load_run(db, target.run.id)
                    assert run is not None
                    evaluation = await build_context(db, run)
                except SkillEvaluationExecutionError:
                    denied = True
            allowed = case.state == "active"
            private = [
                i
                for i, s in enumerate(sql)
                if any(
                    column in s.lower()
                    for column in (
                        "skill_evaluation_sets.evals",
                        "skills.name",
                        "skill_evaluation_runs.run_config",
                        "skill_evaluation_runs.summary",
                        "skill_evaluation_runs.benchmark",
                        "skill_evaluation_runs.usage",
                        "skill_evaluation_runs.case_results",
                    )
                )
            ]
            actor = [
                i
                for i, s in enumerate(sql)
                if "organization_members" in s.lower() or "users.is_active" in s.lower()
            ]
            assert denied != allowed
            assert pending in db.new
            assert not any(
                s.lstrip().lower().startswith(("insert", "update", "delete")) for s in sql
            )
            if allowed:
                assert evaluation.skill_id == target.skill_id
                assert evaluation.skill_version == "read-order-1"
                assert evaluation.skill_content_hash == "a" * 64
                assert evaluation.baseline_comparison is False
                assert evaluation.evals == [{"input": "PRIVATE_EVALUATION_INPUT"}]
                assert "f2-evaluation" in evaluation.runtime_context.descriptors
                assert private and actor and actor[0] < private[0]
                await db.commit()
            else:
                assert private == [], (
                    "Revoked actor hydrated private Skill and evaluation-set content before denial"
                )
                await db.rollback()
            async with stack.sessions() as verifier:
                assert (
                    await verifier.scalar(select(Tool.id).where(Tool.id == pending_id)) is not None
                ) == allowed
