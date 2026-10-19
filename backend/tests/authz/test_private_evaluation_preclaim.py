"""Independent current-SHA controls for pre-execution evaluation authority."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.audit_event import AuditEvent
from app.models.organization import OrganizationMember
from app.models.skill_evaluation import SkillEvaluationRun, SkillEvaluationSet
from app.services import skill_evaluation_service, skill_evaluation_worker
from app.services.skill_evaluation_worker_types import (
    SkillEvaluationContext,
    SkillEvaluationResult,
)
from app.skills import service as skill_service
from app.skills.creation import TextSkillCreation

if TYPE_CHECKING:
    from tests.authz.private_scope_fixture import PrivateScopes

pytest_plugins = ["tests.authz.private_scope_fixture"]


@dataclass(slots=True)
class ProbeEvaluator:
    """Mutable call count records whether the worker crossed the evaluator boundary."""

    calls: int = 0

    async def evaluate(
        self, db: AsyncSession, context: SkillEvaluationContext
    ) -> SkillEvaluationResult:
        self.calls += 1
        return SkillEvaluationResult(
            summary={"case_count": len(context.evals), "pass_rate": 1},
            benchmark={"with_skill_pass_rate": 1},
            case_results=[],
        )


@pytest.mark.parametrize("condition", ["active", "revoked", "foreign_set", "cached_foreign_set"])
async def test_evaluation_validates_authority_before_start_commit(
    db: AsyncSession,
    private_scopes: PrivateScopes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    condition: Literal["active", "revoked", "foreign_set", "cached_foreign_set"],
) -> None:
    monkeypatch.setattr(settings, "data_root", str(tmp_path))
    skill = await skill_service.create_text_skill(
        db,
        TextSkillCreation(
            name="F1 evaluation",
            slug="f1-evaluation",
            description="Use when reviewing private evaluation boundaries.",
            content=(
                "---\nname: f1-evaluation\n"
                'description: "Use when reviewing private evaluation boundaries."\n'
                "---\n\nUse when reviewing the evaluation boundary.\n"
            ),
        ),
        private_scopes.first,
    )
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=private_scopes.first.user_id,
        skill=skill,
        name="F1 evaluation cases",
        evals=[{"input": "bounded probe"}],
    )
    run = await skill_evaluation_service.create_run(
        db,
        user_id=private_scopes.first.user_id,
        skill=skill,
        evaluation_set=evaluation_set,
    )
    run_id = run.id
    await db.commit()
    match condition:
        case "active":
            pass
        case "revoked":
            await db.execute(
                update(OrganizationMember)
                .where(
                    OrganizationMember.org_id == private_scopes.first.org_id,
                    OrganizationMember.user_id == private_scopes.first.user_id,
                )
                .values(status="suspended")
            )
            await db.commit()
        case "foreign_set" | "cached_foreign_set":
            foreign_skill = await skill_service.create_text_skill(
                db,
                TextSkillCreation(
                    name="F1 foreign skill",
                    slug="f1-foreign-skill",
                    description="Use when reviewing foreign parent rejection.",
                    content=(
                        "---\nname: f1-foreign-skill\n"
                        'description: "Use when reviewing foreign parent rejection."\n'
                        "---\n\nUse when reviewing foreign parent rejection.\n"
                    ),
                ),
                private_scopes.second,
            )
            match condition:
                case "foreign_set":
                    evaluation_set.skill_id = foreign_skill.id
                case "cached_foreign_set":
                    await db.execute(
                        update(SkillEvaluationSet)
                        .where(SkillEvaluationSet.id == evaluation_set.id)
                        .values(skill_id=foreign_skill.id)
                        .execution_options(synchronize_session=False)
                    )
                    assert evaluation_set.skill_id == skill.id
                case unreachable:
                    assert_never(unreachable)
            await db.commit()
        case unreachable:
            assert_never(unreachable)
    evaluator = ProbeEvaluator()
    worker = skill_evaluation_worker.SkillEvaluationWorker(evaluator=evaluator)
    await worker.run_once(db, run_id)
    match condition:
        case "active":
            await db.commit()
        case "revoked" | "foreign_set" | "cached_foreign_set":
            # Remove noncommitted cleanup to observe the durable execution claim.
            await db.rollback()
        case unreachable:
            assert_never(unreachable)
    db.expunge_all()
    persisted = await db.get(SkillEvaluationRun, run_id)
    assert persisted is not None
    actions = list(
        (await db.scalars(select(AuditEvent.action).where(AuditEvent.run_id == str(run_id)))).all()
    )
    print(
        f"condition={condition} durable_status={persisted.status} "
        f"started={persisted.started_at is not None} evaluator_calls={evaluator.calls} "
        f"audit_actions={actions}"
    )
    match condition:
        case "active":
            assert persisted.status == "completed"
            assert evaluator.calls == 1
        case "revoked" | "foreign_set" | "cached_foreign_set":
            assert evaluator.calls == 0
            assert persisted.status == "queued", (
                "unauthorized queued work committed a new execution claim"
            )
            assert persisted.started_at is None
            assert "skill_evaluation.run_start" not in actions
        case unreachable:
            assert_never(unreachable)
