"""Scheduled and webhook starts of workflows (workflow-starts-sharing-plan 3.1-3.2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent
from app.models.notification import Notification
from app.models.workflow import WorkflowRun
from app.models.workflow_start import WorkflowSchedule
from app.workflows import starts
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, seed_agent
from tests.sqlite_test_engine import TestSession
from tests.test_workflow_runs import FakeAgent, _job
from tests.workflow_fixtures import inquiry_workflow

BASE = "/api/workflows"


@pytest.fixture(autouse=True)
def _sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.database.async_session", TestSession)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeAgent:
    from app.workflows import agent_step

    agent = FakeAgent()
    monkeypatch.setattr(agent_step, "execute_agent_invoke_structured", agent)
    return agent


async def _published(client: AsyncClient, db: AsyncSession) -> str:
    _, _, agent = await seed_agent(db)
    await db.commit()
    created = (await client.post(BASE, json={"name": "문의 처리"})).json()
    saved = (
        await client.put(
            f"{BASE}/{created['id']}/draft",
            json={"revision": 1, "definition": inquiry_workflow(str(agent.id))},
        )
    ).json()
    published = await client.post(
        f"{BASE}/{created['id']}/publish", json={"revision": saved["draft_revision"]}
    )
    assert published.status_code == 201, published.text
    return str(created["id"])


def _schedule(**overrides: Any) -> dict[str, Any]:
    return {
        "name": "매일 아침",
        "kind": "interval",
        "config": {"minutes": 60},
        "inputs": {"inquiry": "정기 점검"},
        **overrides,
    }


# --- timing -------------------------------------------------------------------------------


def test_next_fire_follows_the_kind_and_time_zone() -> None:
    noon = datetime(2026, 11, 9, 3, 0)  # 12:00 in Seoul
    assert starts.next_fire("interval", {"minutes": 30}, "Asia/Seoul", noon) == datetime(
        2026, 11, 9, 3, 30
    )
    # 09:00 every day in Seoul is 00:00 UTC: tomorrow's, since today's has passed.
    daily = starts.next_fire("cron", {"expression": "0 9 * * *"}, "Asia/Seoul", noon)
    assert daily == datetime(2026, 11, 10, 0, 0)
    later = (noon + timedelta(hours=1)).replace(tzinfo=UTC).isoformat()
    assert starts.next_fire("once", {"at": later}, "Asia/Seoul", noon) == noon + timedelta(hours=1)
    assert starts.next_fire("once", {"at": later}, "Asia/Seoul", noon + timedelta(hours=2)) is None


@pytest.mark.parametrize(
    ("kind", "config"),
    [
        ("interval", {"minutes": 1}),
        ("interval", {"minutes": "60"}),
        ("cron", {"expression": "every day"}),
        ("once", {"at": "2020-01-01T09:00:00+09:00"}),
        ("weekly", {}),
    ],
)
def test_bad_timing_is_refused(kind: str, config: dict[str, Any]) -> None:
    with pytest.raises(starts.AppError) as raised:
        starts.check_timing(kind, config, "Asia/Seoul")
    assert raised.value.status == 422


# --- schedules ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedules_are_checked_against_the_published_inputs(
    client: AsyncClient, db: AsyncSession
) -> None:
    workflow_id = await _published(client, db)
    url = f"{BASE}/{workflow_id}/schedules"
    bad = await client.post(url, json=_schedule(inputs={"nope": 1}))
    assert (bad.status_code, bad.json()["error"]["code"]) == (422, "WORKFLOW_INPUT_INVALID")
    fast = await client.post(url, json=_schedule(config={"minutes": 2}))
    assert fast.status_code == 422
    made = await client.post(url, json=_schedule())
    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["status"], body["failure_count"]) == ("active", 0)
    assert body["next_run_at"] is not None
    listed = (await client.get(url)).json()
    assert [item["id"] for item in listed] == [body["id"]]
    paused = await client.patch(f"/api/workflow-schedules/{body['id']}", json={"status": "paused"})
    assert (paused.json()["status"], paused.json()["next_run_at"]) == ("paused", None)
    gone = await client.delete(f"/api/workflow-schedules/{body['id']}")
    assert gone.status_code == 204
    assert (await client.get(url)).json() == []


@pytest.mark.asyncio
async def test_a_draft_only_workflow_has_no_schedule(client: AsyncClient) -> None:
    created = (await client.post(BASE, json={"name": "초안"})).json()
    made = await client.post(f"{BASE}/{created['id']}/schedules", json=_schedule(inputs={}))
    assert (made.status_code, made.json()["error"]["code"]) == (409, "WORKFLOW_NOT_PUBLISHED")


async def _due_schedule(
    client: AsyncClient, db: AsyncSession, workflow_id: str
) -> WorkflowSchedule:
    made = (await client.post(f"{BASE}/{workflow_id}/schedules", json=_schedule())).json()
    row = await db.get(WorkflowSchedule, uuid.UUID(made["id"]))
    assert row is not None
    row.next_run_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    await db.commit()
    await db.refresh(row)
    return row


@pytest.mark.asyncio
async def test_a_due_schedule_fires_once_and_moves_on(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    workflow_id = await _published(client, db)
    row = await _due_schedule(client, db, workflow_id)
    seen = row.next_run_at
    assert seen is not None
    assert await starts.fire_due() == {"started": 1}
    # A second sweep that read the same time (a second leader) does not start it again.
    async with TestSession() as other:
        assert await starts.fire_one(other, unit_context(TEST_USER_ID), row.id, seen) == "skipped"
    await db.refresh(row)
    assert row.next_run_at is not None and row.next_run_at > seen
    runs = (await db.execute(sa.select(WorkflowRun))).scalars().all()
    assert len(runs) == 1
    run = runs[0]
    assert (run.started_from, run.schedule_id, run.input) == (
        "schedule",
        row.id,
        {"inquiry": "정기 점검"},
    )
    assert row.last_run_id == run.id
    audit = await db.scalar(
        sa.select(AuditEvent).where(
            AuditEvent.action == "workflow.run_start", AuditEvent.target_id == str(run.id)
        )
    )
    assert audit is not None and audit.event_metadata["started_from"] == "schedule"
    fake.answers.append({"category": "bug", "urgent": False})
    assert await _job(str(run.id)) == {"outcome": "completed"}


@pytest.mark.asyncio
async def test_three_refused_starts_pause_the_schedule_and_tell_its_maker(
    client: AsyncClient, db: AsyncSession
) -> None:
    workflow_id = await _published(client, db)
    row = await _due_schedule(client, db, workflow_id)
    archived = await client.post(f"{BASE}/{workflow_id}/archive")
    assert archived.status_code == 200, archived.text
    outcomes = []
    for _ in range(3):
        await db.refresh(row)
        row.next_run_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
        await db.commit()
        outcomes.append(await starts.fire_due())
    assert outcomes == [{"failed": 1}, {"failed": 1}, {"paused": 1}]
    await db.refresh(row)
    assert (row.status, row.failure_count, row.next_run_at) == ("paused", 3, None)
    assert row.last_error == "보관한 워크플로는 실행할 수 없습니다"
    notice = await db.scalar(
        sa.select(Notification).where(Notification.kind == "workflow.schedule_paused")
    )
    assert notice is not None and notice.target_id == row.id
    assert (await db.execute(sa.select(WorkflowRun))).scalars().all() == []


# --- webhooks -------------------------------------------------------------------------------


async def _webhook(client: AsyncClient, workflow_id: str) -> dict[str, Any]:
    made = await client.post(f"{BASE}/{workflow_id}/webhooks", json={"name": "주문 시스템"})
    assert made.status_code == 201, made.text
    return made.json()


@pytest.mark.asyncio
async def test_a_webhook_starts_runs_and_reads_only_its_own(
    client: AsyncClient, db: AsyncSession, fake: FakeAgent
) -> None:
    workflow_id = await _published(client, db)
    hook = await _webhook(client, workflow_id)
    assert hook["token"].startswith("mwh_") and hook["token"].endswith(hook["token_hint"])
    listed = (await client.get(f"{BASE}/{workflow_id}/webhooks")).json()
    assert "token" not in listed[0]  # shown once
    url = f"/hooks/workflows/{hook['public_id']}/runs"
    auth = {"Authorization": f"Bearer {hook['token']}"}
    no_token = await client.post(url, json={"inputs": {"inquiry": "x"}})
    wrong = await client.post(
        url, json={"inputs": {"inquiry": "x"}}, headers={"Authorization": "Bearer nope"}
    )
    nowhere = await client.post(
        "/hooks/workflows/unknown/runs", json={"inputs": {"inquiry": "x"}}, headers=auth
    )
    assert {r.status_code for r in (no_token, wrong, nowhere)} == {404}
    bad_input = await client.post(url, json={"inputs": {}}, headers=auth)
    assert bad_input.status_code == 422
    started = await client.post(url, json={"inputs": {"inquiry": "배송 문의"}}, headers=auth)
    assert started.status_code == 202, started.text
    run_id = started.json()["run_id"]
    fake.answers.append({"category": "other", "urgent": False})
    assert await _job(run_id) == {"outcome": "completed"}
    read = await client.get(f"{url}/{run_id}", headers=auth)
    assert read.json()["status"] == "completed"
    assert read.json()["output"] == {"category": "other", "note": "분류: other"}
    run = await db.get(WorkflowRun, uuid.UUID(run_id))
    assert run is not None and (run.started_from, str(run.webhook_id)) == ("webhook", hook["id"])
    # A run it did not start reads as missing.
    manual = (
        await client.post(f"{BASE}/{workflow_id}/runs", json={"inputs": {"inquiry": "y"}})
    ).json()
    assert (await client.get(f"{url}/{manual['id']}", headers=auth)).status_code == 404


@pytest.mark.asyncio
async def test_a_disabled_or_rotated_webhook_refuses_the_old_token(
    client: AsyncClient, db: AsyncSession
) -> None:
    workflow_id = await _published(client, db)
    hook = await _webhook(client, workflow_id)
    url = f"/hooks/workflows/{hook['public_id']}/runs"
    old = {"Authorization": f"Bearer {hook['token']}"}
    rotated = (await client.post(f"/api/workflow-webhooks/{hook['id']}/token")).json()
    assert (
        await client.post(url, json={"inputs": {"inquiry": "x"}}, headers=old)
    ).status_code == 404
    new = {"Authorization": f"Bearer {rotated['token']}"}
    disabled = await client.patch(
        f"/api/workflow-webhooks/{hook['id']}", json={"status": "disabled"}
    )
    assert disabled.json()["status"] == "disabled"
    assert (
        await client.post(url, json={"inputs": {"inquiry": "x"}}, headers=new)
    ).status_code == 404
    await client.patch(f"/api/workflow-webhooks/{hook['id']}", json={"status": "active"})
    assert (
        await client.post(url, json={"inputs": {"inquiry": "x"}}, headers=new)
    ).status_code == 202
