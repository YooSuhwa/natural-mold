"""A completed predecessor may not satisfy a revised goal using pre-steer evidence."""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from deepagents import create_deep_agent
from httpx import AsyncClient
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import chat_steer_middleware
from app.agent_runtime.chat_steer_checkpoint import confirmed_steer_ids
from app.config import settings
from app.models.conversation import Conversation
from app.models.conversation_goal import GoalAttempt, GoalTransitionIntent
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_service as runs
from app.services.conversation_goal_worker import reconcile_goal_run
from app.services.conversation_run_queue_claim import claim_next_input
from app.services.conversation_run_steer_rollover import reconcile_terminal_steers
from app.services.conversation_run_steers import get_conversation_steer_watermarks
from app.tenancy.checkpoint_audit import run_configurable
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.sqlite_test_engine import TestSession
from tests.test_conversation_goal_worker import _accept
from tests.test_conversation_goals import _recorder
from tests.test_guardrail_middleware import RecordingModel

_DIRECTIVE = "국내 업체 세 곳의 최신 견적을 비교해 줘."
_FILE = "comparison.csv"


async def _record_table(
    conversation: Conversation, run: ConversationRun, directory: Path, content: bytes
) -> None:
    recorder = await _recorder(conversation, run, directory)
    (directory / _FILE).write_bytes(content)
    assert await recorder.collect_after_tool_result(tool_name="write_file", tool_call_id="write")


async def _deliver_at_model_boundary(
    run: ConversationRun, receipt_id: UUID, tenant_id: UUID
) -> None:
    model = RecordingModel(responses=[AIMessage(content="최신 견적을 다시 확인하겠습니다.")])
    saver = InMemorySaver()
    graph = create_deep_agent(
        model=model, middleware=[chat_steer_middleware.RunSteerMiddleware()], checkpointer=saver
    )
    config: RunnableConfig = {
        "configurable": run_configurable(str(run.conversation_id), run.id, tenant_id)
    }
    await graph.ainvoke(
        {"messages": [HumanMessage(content="업체를 비교해 줘.")]}, config, durability="sync"
    )
    user_inputs = [m.content for call in model.seen for m in call if isinstance(m, HumanMessage)]
    assert user_inputs.count(_DIRECTIVE) == 1
    assert await confirmed_steer_ids(
        run.conversation_id, run.id, [receipt_id], saver=saver, tenant_id=tenant_id
    ) == {receipt_id}


async def _complete_and_redeliver(db: AsyncSession, run: ConversationRun) -> None:
    await runs.transition_run(db, run, "completed")
    await db.commit()
    for _ in range(2):
        await reconcile_goal_run(db, run.id, run.conversation_id)
        await db.commit()


async def _require_domestic_csv(client: AsyncClient, goal_url: str, revision: int) -> None:
    changed = await client.put(
        goal_url,
        json={
            "expected_revision": revision,
            "title": "국내 세 업체 최신 견적",
            "criteria": [
                {
                    "kind": "csv_table",
                    "logical_path": _FILE,
                    "required_columns": ["company", "country", "price"],
                    "unique_by": "company",
                    "minimum_rows": 3,
                    "column_equals": {"country": "KR"},
                }
            ],
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["goal"]["version"] == 2


@dataclass(frozen=True)
class PendingSteer:
    conversation: Conversation
    run: ConversationRun
    receipt_id: UUID
    goal_url: str
    revision: int


@pytest.fixture
async def pending_steer(
    db: AsyncSession,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> PendingSteer:
    context = unit_context(TEST_USER_ID)
    monkeypatch.setattr(settings, "conversation_goal_org_ids", frozenset({context.org_id}))
    monkeypatch.setattr(settings, "chat_steer_enabled", True)
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "objects"))
    monkeypatch.setattr(chat_steer_middleware, "tenant_session", lambda _: TestSession())
    conversation_id, _ = await seed_artifact_conversation()
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    goal_url = f"/api/conversations/{conversation_id}/goal"
    created = await client.put(
        goal_url,
        json={
            "title": "업체 비교",
            "expected_revision": 0,
            "criteria": [{"kind": "nonempty_file", "logical_path": _FILE}],
        },
    )
    assert created.status_code == 200, created.text
    run = await _accept(db, conversation)
    await runs.transition_run(db, run, "running", worker_instance_id="first")
    await db.commit()
    # Even the old table passes every structural check in the revised goal.
    old_table = b"company,country,price\nAcme,KR,20\nBeta,KR,10\nGamma,KR,30\n"
    await _record_table(conversation, run, tmp_path / "old", old_table)

    steer_url = f"/api/conversations/{conversation_id}/runs/{run.id}/steers"
    request = {"client_request_id": str(uuid4()), "text": _DIRECTIVE}
    receipt = await client.post(steer_url, json=request)
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["status"] == "pending"
    duplicate = await client.post(steer_url, json=request)
    assert duplicate.json() == receipt.json()
    pending = await get_conversation_steer_watermarks(db, conversation_id)
    assert (pending.latest_received, pending.latest_applied, pending.has_unresolved) == (1, 0, True)
    await db.commit()
    return PendingSteer(
        conversation, run, UUID(receipt.json()["id"]), goal_url, created.json()["goal"]["revision"]
    )


async def test_unconsumed_steer_rolls_over_without_starting_a_goal_followup(
    db: AsyncSession, client: AsyncClient, pending_steer: PendingSteer
) -> None:
    run = pending_steer.run
    await runs.transition_run(db, run, "completed")
    await db.commit()
    for _ in range(2):
        await reconcile_terminal_steers(
            db,
            conversation_id=run.conversation_id,
            run_id=run.id,
            context=unit_context(TEST_USER_ID),
        )
        await reconcile_goal_run(db, run.id, run.conversation_id)
        await db.commit()
    rolled = (await db.scalars(select(ConversationRunInput))).one()
    assert (rolled.source, rolled.status) == ("steer", "pending")
    response = await client.get(pending_steer.goal_url)
    assert response.status_code == 200, response.text
    goal = response.json()["goal"]
    assert (goal["status"], goal["blocked_reason"], goal["auto_followups"]) == (
        "blocked",
        "steer_pending",
        0,
    )
    intent = (await db.scalars(select(GoalTransitionIntent))).one()
    assert (intent.kind, intent.run_id) == ("blocked", run.id)


@pytest.mark.parametrize("edit_goal", [False, True], ids=["steer-only", "steer-and-goal-edit"])
async def test_confirmed_steer_requires_fresh_goal_evidence(
    edit_goal: bool,
    pending_steer: PendingSteer,
    db: AsyncSession,
    client: AsyncClient,
    tmp_path: Path,
) -> None:
    conversation, run = pending_steer.conversation, pending_steer.run
    conversation_id, goal_url = conversation.id, pending_steer.goal_url
    context = unit_context(TEST_USER_ID)
    await _deliver_at_model_boundary(run, pending_steer.receipt_id, context.tenant_id)
    applied = await get_conversation_steer_watermarks(db, conversation_id)
    assert (applied.latest_received, applied.latest_applied, applied.has_unresolved) == (
        1,
        1,
        False,
    )
    await db.commit()

    version = 1
    if edit_goal:
        await _require_domestic_csv(client, goal_url, pending_steer.revision)
        version = 2
    await _complete_and_redeliver(db, run)
    queued = (await db.scalars(select(ConversationRunInput))).one()
    assert (queued.source, queued.status) == ("goal_followup", "pending")
    assert list(await db.scalars(select(GoalTransitionIntent))) == []
    attempt = (
        await db.scalars(select(GoalAttempt).where(GoalAttempt.status == "followup_reserved"))
    ).one()
    assert (attempt.goal_version, attempt.steer_seq, attempt.followup_ordinal) == (version, 1, 1)

    claim = await claim_next_input(db, conversation_id=conversation_id, context=context)
    assert claim and claim.input.id == queued.id
    fresh = claim.run
    assert fresh.id != run.id
    await runs.transition_run(db, fresh, "running", worker_instance_id="second")
    await db.commit()
    new_table = b"company,country,price\nAcme,KR,40\nBeta,KR,120\nGamma,KR,65\n"
    await _record_table(conversation, fresh, tmp_path / "fresh", new_table)
    await _complete_and_redeliver(db, fresh)

    view = await client.get(goal_url)
    assert view.status_code == 200, view.text
    goal = view.json()["goal"]
    assert (goal["version"], goal["status"], goal["auto_followups"]) == (version, "completed", 1)
    (check,) = goal["last_evidence"]["checks"]
    assert check["passed"]
    artifact_url = f"{goal_url}/artifacts/{check['artifact_id']}"
    downloaded = await client.get(f"{artifact_url}/versions/{check['version_id']}/download")
    assert downloaded.status_code == 200 and downloaded.content == new_table
    intent = (await db.scalars(select(GoalTransitionIntent))).one()
    assert (intent.kind, intent.goal_version, intent.run_id) == ("completed", version, fresh.id)
