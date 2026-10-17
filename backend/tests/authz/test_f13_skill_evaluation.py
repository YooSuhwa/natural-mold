"""F-13: ``SkillEvaluationAccessPlanner`` re-decides a queued evaluation when it executes.

The creator's ``skill#can_edit`` is checked by the worker, so a run queued before the
permission was revoked fails before the evaluator mounts or executes the skill; an
undecidable run fails with the retryable ``authz_unavailable`` code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.fake import FakeAuthz
from app.authz.service import AuthzService
from app.models.audit_event import AuditEvent
from app.models.skill_evaluation import SkillEvaluationRun
from app.services import skill_evaluation_service
from app.services.skill_evaluation_worker import SkillEvaluationWorker
from app.services.skill_evaluation_worker_state import build_context
from app.services.skill_evaluation_worker_types import SkillEvaluationContext, SkillEvaluationResult
from tests.authz.agent_route_fixture import DownAuthz

if TYPE_CHECKING:
    from tests.authz.test_private_skill_scope import SkillCase

pytest_plugins = ["tests.authz.test_private_skill_scope"]


class NeverEvaluate:
    async def evaluate(
        self, db: AsyncSession, context: SkillEvaluationContext
    ) -> SkillEvaluationResult:
        raise AssertionError("a revoked run must fail before evaluation")


@pytest.fixture
async def queued(db: AsyncSession, skill_case: SkillCase) -> SkillEvaluationRun:
    skill = skill_case.first
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db, user_id=skill.user_id, skill=skill, name="F-13", evals=[{"input": "Fixture"}]
    )
    run = await skill_evaluation_service.create_run(
        db, user_id=skill.user_id, skill=skill, evaluation_set=evaluation_set
    )
    await db.commit()
    return run


def _enforce(monkeypatch: pytest.MonkeyPatch, *allowed: tuple[str, str, str]) -> None:
    service = AuthzService(FakeAuthz(allowed=frozenset(allowed)), mode="enforce")
    monkeypatch.setattr("app.authz.runtime._current", service)


async def test_evaluation_run_refuses_after_can_edit_is_revoked(
    db: AsyncSession, queued: SkillEvaluationRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    creator, obj = f"user:{queued.user_id}", f"skill:{queued.skill_id}"
    _enforce(monkeypatch, (creator, "can_use", obj), (creator, "can_edit", obj))
    context = await build_context(db, queued)
    assert context.skill_id == queued.skill_id

    _enforce(monkeypatch, (creator, "can_use", obj))  # can_edit revoked after queueing
    completed = await SkillEvaluationWorker(evaluator=NeverEvaluate()).run_once(db, queued.id)
    assert completed is not None and completed.status == "failed"
    assert "permission_required" in (completed.error_message or "")


async def _fails_retryable(db: AsyncSession, run: SkillEvaluationRun) -> None:
    completed = await SkillEvaluationWorker(evaluator=NeverEvaluate()).run_once(db, run.id)
    assert completed is not None and completed.status == "failed"
    assert (completed.error_message or "").startswith("authz_unavailable")
    events = (
        await db.scalars(select(AuditEvent).where(AuditEvent.action == "skill_evaluation.run_fail"))
    ).all()
    assert [(event.event_metadata or {}).get("reason_code") for event in events] == [
        "AUTHZ_UNAVAILABLE"
    ]


async def test_an_openfga_outage_is_a_retryable_failure(
    db: AsyncSession, queued: SkillEvaluationRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.authz.runtime._current", AuthzService(DownAuthz(), mode="enforce"))
    await _fails_retryable(db, queued)


async def test_a_stopped_authz_runtime_fails_the_run_cleanly(
    db: AsyncSession, queued: SkillEvaluationRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    def not_running() -> AuthzService:
        raise RuntimeError("AuthzService is not running")

    monkeypatch.setattr("app.authz.runtime.runtime_authz", not_running)
    await _fails_retryable(db, queued)
