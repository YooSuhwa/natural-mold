from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal, assert_never
from unittest.mock import patch

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.skill_builder.eval_cancellation import (
    EvalCancellationCheckpoint,
    EvalCancellationPhase,
    EvalRunCancelled,
)
from app.models.organization import OrganizationMember
from app.models.skill_evaluation import SkillEvaluationRun
from app.services import skill_evaluation_service
from app.services.skill_evaluation_worker import SkillEvaluationWorker
from app.services.skill_evaluation_worker_outcomes import cancel_if_requested
from app.services.skill_evaluation_worker_state import (
    DbSkillEvaluationCancellationProbe,
    mark_completed,
)
from app.services.skill_evaluation_worker_types import SkillEvaluationResult
from app.skills import service as skill_service
from app.skills.creation import PackageSkillCreation
from tests.authz.queue_resource_fixture import queue_resource_sql
from tests.authz.unit_scope_fixture import create_test_package_skill, unit_context
from tests.conftest import TEST_USER_ID, TestSession

if TYPE_CHECKING:
    from tests.authz.test_private_evaluation_read_order import Target

pytestmark = pytest.mark.asyncio
pytest_plugins = ["tests.authz.test_private_evaluation_read_order"]


def _skill_content() -> str:
    return (
        "---\n"
        "name: evaluation-state\n"
        'description: "Use when testing evaluation state races."\n'
        "---\n\n"
        "Use when testing evaluation state races.\n"
    )


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", _skill_content())
    return buffer.getvalue()


async def test_mark_completed_preserves_concurrent_cancel(
    db: AsyncSession,
    tmp_path: Path,
) -> None:
    with patch.object(skill_service.settings, "data_root", str(tmp_path)):
        skill = await create_test_package_skill(
            db, PackageSkillCreation(zip_bytes=_zip_bytes()), unit_context(TEST_USER_ID)
        )
    evaluation_set = await skill_evaluation_service.create_evaluation_set(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        name="Race",
        evals=[{"input": "race"}],
    )
    run = await skill_evaluation_service.create_run(
        db,
        user_id=TEST_USER_ID,
        skill=skill,
        evaluation_set=evaluation_set,
    )
    run.status = "grading"
    await db.commit()

    stale_run = await db.get(SkillEvaluationRun, run.id)
    assert stale_run is not None
    async with TestSession() as other:
        cancelling = await other.get(SkillEvaluationRun, run.id)
        assert cancelling is not None
        await skill_evaluation_service.cancel_run(other, cancelling, reason="user")
        await other.commit()

    completed = await mark_completed(
        db,
        stale_run,
        SkillEvaluationResult(
            summary={"case_count": 1, "pass_rate": 1},
            benchmark={"with_skill_pass_rate": 1, "without_skill_pass_rate": 0},
            case_results=[],
        ),
    )
    await db.commit()
    await db.refresh(stale_run)

    assert completed is False
    assert stale_run.status == "cancelled"
    assert stale_run.cancellation_requested_at is not None


async def test_worker_start_skips_when_leader_lock_is_not_acquired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def deny_leadership() -> bool:
        return False

    monkeypatch.setattr(
        "app.services.skill_evaluation_worker.try_acquire_skill_evaluation_worker_leader",
        deny_leadership,
    )
    worker = SkillEvaluationWorker()

    await worker.start()

    assert worker._task is None


async def test_worker_start_does_not_reconcile_when_leader_lock_is_not_acquired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def deny_leadership() -> bool:
        return False

    monkeypatch.setattr(
        "app.services.skill_evaluation_worker.try_acquire_skill_evaluation_worker_leader",
        deny_leadership,
    )

    reconcile_calls = 0

    class RecordingWorker(SkillEvaluationWorker):
        async def reconcile_stale_runs(self, *_args: object, **_kwargs: object) -> int:
            nonlocal reconcile_calls
            reconcile_calls += 1
            return 0

    worker = RecordingWorker()

    started = await worker.start(reconcile_stale=True)

    assert started is False
    assert worker._task is None
    assert reconcile_calls == 0


@pytest.mark.parametrize("action", ["probe", "cancel", "recover", "denied_worker"])
async def test_evaluation_maintenance_avoids_private_json_after_membership_loss(
    db: AsyncSession, target: Target, action: Literal["probe", "cancel", "recover", "denied_worker"]
) -> None:
    own = target.scopes.first
    status = (
        "cancelled"
        if action in ("probe", "cancel")
        else "running"
        if action == "recover"
        else "queued"
    )
    async with TestSession() as writer:
        await writer.execute(
            update(OrganizationMember)
            .where(
                OrganizationMember.org_id == own.org_id,
                OrganizationMember.user_id == own.user_id,
            )
            .values(status="removed")
        )
        await writer.execute(
            update(SkillEvaluationRun)
            .where(
                SkillEvaluationRun.id == target.run.id,
            )
            .values(status=status)
        )
        await writer.commit()
    worker = SkillEvaluationWorker()
    with queue_resource_sql(db) as statements:
        match action:
            case "probe":
                with pytest.raises(EvalRunCancelled):
                    await DbSkillEvaluationCancellationProbe(db, target.run.id).raise_if_cancelled(
                        EvalCancellationCheckpoint(EvalCancellationPhase.START),
                    )
            case "cancel":
                assert await cancel_if_requested(db, target.run)
            case "recover":
                assert await worker.mark_interrupted_runs(db) == 1
            case "denied_worker":
                run = await worker.run_once(db, target.run.id)
                assert run is not None and run.status == "failed"
                await worker._record_usage_ledger_after_commit(run, target.scopes.first.tenant_id)
            case unreachable:
                assert_never(unreachable)
    private_selects = [
        s
        for s in statements
        if s.lstrip().lower().startswith("select")
        and any(
            column in s.lower()
            for column in (
                "skill_evaluation_runs.run_config",
                "skill_evaluation_runs.summary",
                "skill_evaluation_runs.benchmark",
                "skill_evaluation_runs.usage",
                "skill_evaluation_runs.case_results",
                "skill_evaluation_sets.evals",
                "skills.name",
            )
        )
    ]
    assert private_selects == []
    await db.commit()
    async with TestSession() as verifier:
        expected = "cancelled" if action in ("probe", "cancel") else "failed"
        assert (
            await verifier.scalar(
                select(SkillEvaluationRun.status).where(
                    SkillEvaluationRun.id == target.run.id,
                )
            )
            == expected
        )
