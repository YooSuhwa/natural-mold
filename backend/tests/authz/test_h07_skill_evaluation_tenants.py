"""H-07 (plan 8.8/8.9): evaluation queue items are company-keyed and run per company."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import REASON_INFO_KEY, SCOPE_INFO_KEY
from app.models.skill import Skill
from app.models.skill_evaluation import SkillEvaluationRun
from app.services import skill_evaluation_worker_tenants as tenants_module
from app.services.skill_evaluation_queue import SkillEvaluationQueueItem
from app.services.skill_evaluation_worker import SkillEvaluationWorker
from tests.authz.unit_scope_fixture import TEST_TENANT_ID
from tests.conftest import TEST_USER_ID, TestSession
from tests.skill_evaluation_worker_helpers import create_run

TENANT_B = uuid.uuid4()


class _RecordingMaker:
    def __init__(self) -> None:
        self.infos: list[dict[str, Any]] = []

    def __call__(self) -> AsyncSession:
        session = TestSession()
        self.infos.append(session.info)
        return session


def test_queue_items_require_a_tenant_id() -> None:
    worker = SkillEvaluationWorker(queue_max_size=5)
    with pytest.raises(TypeError):
        worker.enqueue(uuid.uuid4(), user_id=TEST_USER_ID)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="tenant_id"):
        worker.enqueue(uuid.uuid4(), tenant_id="x", user_id=TEST_USER_ID)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="user_id"):
        SkillEvaluationQueueItem(uuid.uuid4(), TEST_TENANT_ID, None)  # type: ignore[arg-type]
    assert worker.pop_next() is None


def test_queue_items_keep_their_company() -> None:
    worker = SkillEvaluationWorker(queue_max_size=5)
    first, second = uuid.uuid4(), uuid.uuid4()
    worker.enqueue(first, tenant_id=TEST_TENANT_ID, user_id=TEST_USER_ID)
    worker.enqueue(second, tenant_id=None, user_id=TEST_USER_ID)
    assert worker.pop_next() == SkillEvaluationQueueItem(first, TEST_TENANT_ID, TEST_USER_ID)
    assert worker.pop_next() == SkillEvaluationQueueItem(second, None, TEST_USER_ID)


async def test_each_item_runs_in_its_skills_company_as_the_requester(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[uuid.UUID, dict[str, Any]]] = []

    async def fake_run_once(self: SkillEvaluationWorker, db: AsyncSession, run_id: uuid.UUID):
        seen.append((run_id, dict(db.info)))

    monkeypatch.setattr(SkillEvaluationWorker, "run_once", fake_run_once)
    worker = SkillEvaluationWorker()
    worker._session_factory = TestSession
    items = [
        SkillEvaluationQueueItem(uuid.uuid4(), TEST_TENANT_ID, TEST_USER_ID),
        SkillEvaluationQueueItem(uuid.uuid4(), TENANT_B, TEST_USER_ID),
        SkillEvaluationQueueItem(uuid.uuid4(), None, TEST_USER_ID),
    ]
    for item in items:
        await worker._execute_run_with_session(item)
    user = str(TEST_USER_ID)
    assert [(run_id, info[SCOPE_INFO_KEY]) for run_id, info in seen] == [
        (items[0].run_id, {"app.tenant_id": str(TEST_TENANT_ID), "app.user_id": user}),
        (items[1].run_id, {"app.tenant_id": str(TENANT_B), "app.user_id": user}),
        (items[2].run_id, {"app.scope": "platform"}),
    ]
    assert seen[2][1][REASON_INFO_KEY] == "skill_evaluation.system_skill"


async def _interrupted_runs(db: AsyncSession, tmp_path: Path) -> dict[uuid.UUID, uuid.UUID | None]:
    runs = [await create_run(db, tmp_path) for _ in range(3)]
    company_of = {runs[0].id: TEST_TENANT_ID, runs[1].id: TENANT_B, runs[2].id: None}
    for run in runs:
        tenant_id = company_of[run.id]
        values: dict[str, Any] = {"tenant_id": tenant_id}
        if tenant_id is None:  # a system skill has no organization or company
            values |= {"is_system": True, "org_id": None}
        await db.execute(update(Skill).where(Skill.id == run.skill_id).values(**values))
    await db.execute(
        update(SkillEvaluationRun)
        .where(SkillEvaluationRun.id.in_(list(company_of)))
        .values(status="running")
    )
    await db.commit()
    return company_of


async def test_reconcile_uses_the_workers_company_rule_per_company(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_of = await _interrupted_runs(db, tmp_path)
    failed: list[tuple[uuid.UUID, dict[str, Any]]] = []
    original = tenants_module.mark_failed

    async def recording_mark_failed(session: AsyncSession, run: SkillEvaluationRun, msg: str):
        failed.append((run.id, dict(session.info)))
        return await original(session, run, msg)

    monkeypatch.setattr(tenants_module, "mark_failed", recording_mark_failed)
    maker = _RecordingMaker()
    assert await SkillEvaluationWorker().reconcile_stale_runs(maker) == 3

    assert maker.infos[0][REASON_INFO_KEY] == "skill_evaluation.reconcile"
    assert maker.infos[0][SCOPE_INFO_KEY] == {"app.scope": "platform"}
    assert len(failed) == 3
    for run_id, info in failed:
        company = company_of[run_id]
        if company is None:
            assert info[SCOPE_INFO_KEY] == {"app.scope": "platform"}
            assert info[REASON_INFO_KEY] == "skill_evaluation.system_skill"
        else:
            assert info[SCOPE_INFO_KEY] == {"app.tenant_id": str(company)}
    # One session per company, plus the listing session.
    assert len(maker.infos) == 4


async def test_reconcile_isolates_a_failing_company(
    db: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    company_of = await _interrupted_runs(db, tmp_path)
    original = tenants_module.mark_failed

    async def failing_for_a(session: AsyncSession, run: SkillEvaluationRun, msg: str):
        if company_of[run.id] == TEST_TENANT_ID:
            raise RuntimeError("company A database failure")
        return await original(session, run, msg)

    monkeypatch.setattr(tenants_module, "mark_failed", failing_for_a)
    assert await SkillEvaluationWorker().reconcile_stale_runs(TestSession) == 2
    async with TestSession() as session:
        rows = await session.execute(select(SkillEvaluationRun.id, SkillEvaluationRun.status))
        status = dict(rows.tuples().all())
    for run_id, company in company_of.items():
        assert status[run_id] == ("running" if company == TEST_TENANT_ID else "failed")
