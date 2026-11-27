"""Goal API and committed-run invariants using the private ordinary-chat boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.org_context import OrgContext
from app.config import settings
from app.dependencies import CurrentUser, get_current_user, get_org_context
from app.models.conversation import Conversation
from app.models.conversation_goal import GoalAttempt, GoalTransitionIntent
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.schemas.conversation_scope import ConversationBranchMutation
from app.services import conversation_goal_service as goals
from app.services.artifacts.recorder import ArtifactDeltaRecorder, ArtifactRuntimeContext
from app.services.chat.conversation_mutations import set_active_branch
from app.services.conversation_goal_worker import capture_new_run, enqueue_reserved_followup
from app.services.conversation_run_queue_claim import claim_next_input
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.authz.unit_scope_fixture import TEST_ORG_ID, unit_context
from tests.conftest import TEST_USER_ID, TestSession

CONDITION = {"kind": "nonempty_file", "logical_path": "comparison.csv"}


@pytest.fixture
def steering(monkeypatch: pytest.MonkeyPatch) -> list[goals.GoalSteerState]:
    state = [goals.GoalSteerState(0, 0, False)]

    async def watermarks(
        db: AsyncSession, conversation_id: UUID, after_sequence: int = 0
    ) -> goals.GoalSteerState:
        return state[0]

    monkeypatch.setattr(goals, "_watermarks", watermarks)
    monkeypatch.setattr(settings, "conversation_goal_org_ids", frozenset({TEST_ORG_ID}))
    return state


@pytest.fixture
async def conversation(db: AsyncSession, steering: list[goals.GoalSteerState]) -> Conversation:
    conversation_id, _ = await seed_artifact_conversation()
    row = await db.get(Conversation, conversation_id)
    assert row
    return row


def _url(conversation: Conversation) -> str:
    return f"/api/conversations/{conversation.id}/goal"


def _body(revision: int = 0, title: str = "업체 비교") -> dict:
    return {"title": title, "criteria": [CONDITION], "expected_revision": revision}


async def _goal(client: AsyncClient, conversation: Conversation) -> dict:
    response = await client.put(_url(conversation), json=_body())
    assert response.status_code == 200, response.text
    return response.json()["goal"]


async def _start(
    db: AsyncSession,
    conversation: Conversation,
    *,
    source: str = "chat",
    parent: ConversationRun | None = None,
) -> ConversationRun:
    run = ConversationRun(
        conversation_id=conversation.id,
        agent_id=conversation.agent_id,
        org_id=conversation.org_id,
        tenant_id=conversation.tenant_id,
        user_id=conversation.user_id,
        run_source="CHAT",
        invoker_type="user",
        source=source,
        status="running",
        is_active=True,
        parent_run_id=parent.id if parent else None,
    )
    db.add(run)
    await db.flush()
    await capture_new_run(db, conversation=conversation, run=run, interrupted_parent=parent)
    await db.commit()
    return run


async def _finish(db: AsyncSession, run: ConversationRun, status: str = "completed") -> None:
    run.status = status
    run.is_active = False
    run.completed_at = datetime.now(UTC).replace(tzinfo=None)
    await db.commit()


async def _settle(db: AsyncSession, conversation: Conversation, run: ConversationRun) -> None:
    reservation = await goals.evaluate_goal_attempt(db, conversation=conversation, run=run)
    if reservation:
        goal, attempt = reservation
        await enqueue_reserved_followup(db, conversation, goal, attempt)
    await db.commit()


async def _events(db: AsyncSession) -> list[GoalTransitionIntent]:
    return list(await db.scalars(select(GoalTransitionIntent)))


async def test_lifecycle_revision_and_enrollment(
    client: AsyncClient, conversation: Conversation, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = _url(conversation)
    assert (await client.get(url)).json() == {"enabled": True, "goal": None}
    first = await _goal(client, conversation)
    assert (first["version"], first["revision"], first["status"]) == (1, 1, "active")

    edit = await client.put(url, json=_body(1, "국내 업체만 비교"))
    assert edit.status_code == 200
    second = edit.json()["goal"]
    assert (second["id"], second["version"], second["revision"]) == (first["id"], 2, 2)
    assert (await client.put(url, json=_body(1, "오래된 입력"))).status_code == 409

    for revision, action, status in [
        (2, "pause", "paused"),
        (3, "resume", "active"),
        (4, "cancel", "canceled"),
    ]:
        changed = await client.post(
            f"{url}/actions", json={"expected_revision": revision, "action": action}
        )
        assert changed.status_code == 200, changed.text
        assert (changed.json()["goal"]["status"], changed.json()["goal"]["version"]) == (status, 2)
    assert (
        await client.post(f"{url}/actions", json={"expected_revision": 5, "action": "resume"})
    ).status_code == 409

    monkeypatch.setattr(settings, "conversation_goal_org_ids", frozenset())
    history = await client.get(url)
    assert history.json()["enabled"] is False
    assert history.json()["goal"]["status"] == "canceled"
    assert (await client.put(url, json=_body(5))).status_code == 409


async def test_nonordinary_conversation_cannot_accept_goal(
    db: AsyncSession, client: AsyncClient, conversation: Conversation
) -> None:
    conversation.source = "trigger"
    await db.commit()
    url = _url(conversation)
    assert (await client.put(url, json=_body())).status_code in (404, 409)
    assert await goals.get_goal(db, conversation) is None


@pytest.mark.parametrize("foreign_scope", ["user", "company"])
async def test_other_private_scope_cannot_read_or_edit(
    db: AsyncSession,
    client: AsyncClient,
    test_app: FastAPI,
    conversation: Conversation,
    monkeypatch: pytest.MonkeyPatch,
    foreign_scope: str,
) -> None:
    await _goal(client, conversation)
    if foreign_scope == "user":

        async def foreign_user() -> CurrentUser:
            return CurrentUser(id=other_id, email="unrelated@example.test", name="Other")

        other_id = uuid4()
        monkeypatch.setitem(test_app.dependency_overrides, get_current_user, foreign_user)
    else:
        tenant = Tenant(name="Another", slug="another", secrets_namespace="another")
        db.add(tenant)
        await db.flush()
        org = Organization(tenant_id=tenant.id, name="Another", slug="another")
        db.add(org)
        await db.flush()
        db.add_all(
            [
                TenantMember(tenant_id=tenant.id, user_id=conversation.user_id),
                OrganizationMember(
                    tenant_id=tenant.id, org_id=org.id, user_id=conversation.user_id
                ),
            ]
        )
        await db.commit()

        async def foreign_context() -> OrgContext:
            return OrgContext(org.id, tenant.id, conversation.user_id)

        monkeypatch.setitem(test_app.dependency_overrides, get_org_context, foreign_context)
    url = _url(conversation)
    for response in [await client.get(url), await client.put(url, json=_body(1, "unrelated"))]:
        assert response.status_code == 404
        assert "업체 비교" not in response.text


async def test_pending_steer_blocks_old_completion_and_deduplicates_notice(
    db: AsyncSession,
    client: AsyncClient,
    conversation: Conversation,
    steering: list[goals.GoalSteerState],
) -> None:
    await _goal(client, conversation)
    run = await _start(db, conversation)
    steering[0] = goals.GoalSteerState(1, 0, True)
    await _finish(db, run)
    await _settle(db, conversation, run)
    await _settle(db, conversation, run)
    readback = (await client.get(_url(conversation))).json()["goal"]
    assert (readback["status"], readback["blocked_reason"], readback["auto_followups"]) == (
        "blocked",
        "steer_pending",
        0,
    )
    events = await _events(db)
    assert [event.kind for event in events] == ["blocked"]
    assert events[0].run_id == run.id


async def _recorder(
    conversation: Conversation, run: ConversationRun, output_dir: Path
) -> ArtifactDeltaRecorder:
    await asyncio.to_thread(output_dir.mkdir)
    assert conversation.agent_id
    recorder = ArtifactDeltaRecorder(
        session_factory=TestSession,
        context=ArtifactRuntimeContext(
            conversation_id=conversation.id,
            org_context=unit_context(TEST_USER_ID),
            agent_id=conversation.agent_id,
            output_dir=output_dir,
            assistant_msg_id=str(run.id),
        ),
    )
    await recorder.prepare()
    return recorder


@pytest.mark.parametrize("target_changed", ["confirmed_edit", "delivered_steer"])
async def test_return_after_goal_change_opens_only_fresh_followup_evidence(
    db: AsyncSession,
    client: AsyncClient,
    conversation: Conversation,
    steering: list[goals.GoalSteerState],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_changed: str,
) -> None:
    # The predecessor's already-written bytes meet the file criterion but predate the new request.
    await _goal(client, conversation)
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "store"))
    run = await _start(db, conversation)
    first_dir = tmp_path / "old"
    first = await _recorder(conversation, run, first_dir)
    (first_dir / "comparison.csv").write_text("old results")
    assert await first.collect_after_tool_result(tool_name="write_file", tool_call_id="old")
    if target_changed == "confirmed_edit":
        changed = await client.put(_url(conversation), json=_body(1, "국내 업체만 비교"))
        assert changed.status_code == 200
    else:
        steering[0] = goals.GoalSteerState(1, 1, False)
    expected_version = 2 if target_changed == "confirmed_edit" else 1

    # No browser requests are made until both worker deliveries and the fresh run complete.
    await _finish(db, run)
    await _settle(db, conversation, run)
    await _settle(db, conversation, run)
    queued = (await db.scalars(select(ConversationRunInput))).one()
    assert queued.source == "goal_followup" and queued.status == "pending"
    assert await _events(db) == []
    claim = await claim_next_input(
        db, conversation_id=conversation.id, context=unit_context(TEST_USER_ID)
    )
    assert claim and claim.input.id == queued.id
    await db.commit()
    second_dir = tmp_path / "new"
    second = await _recorder(conversation, claim.run, second_dir)
    (second_dir / "comparison.csv").write_text("fresh results")
    assert await second.collect_after_tool_result(tool_name="write_file", tool_call_id="fresh")
    await _finish(db, claim.run)
    await _settle(db, conversation, claim.run)
    readback = (await client.get(_url(conversation))).json()["goal"]
    assert (readback["version"], readback["status"], readback["auto_followups"]) == (
        expected_version,
        "completed",
        1,
    )
    events = await _events(db)
    assert [(event.kind, event.goal_version, event.run_id) for event in events] == [
        ("completed", expected_version, claim.run.id)
    ]
    check = readback["last_evidence"]["checks"][0]
    artifact_url = f"{_url(conversation)}/artifacts/{check['artifact_id']}"
    download = await client.get(f"{artifact_url}/versions/{check['version_id']}/download")
    assert download.status_code == 200 and download.content == b"fresh results"


async def test_goal_promotes_draft_before_late_cleanup(
    client: AsyncClient, conversation: Conversation
) -> None:
    draft = await client.post(f"/api/agents/{conversation.agent_id}/conversations/draft")
    assert draft.status_code == 201
    url = f"/api/conversations/{draft.json()['id']}"
    accepted = await client.put(f"{url}/goal", json=_body())
    assert accepted.status_code == 200
    assert (await client.delete(url, params={"only_if_draft": "true"})).status_code == 204
    readback = await client.get(f"{url}/goal")
    assert (
        readback.status_code == 200
        and readback.json()["goal"]["id"] == accepted.json()["goal"]["id"]
    )


@pytest.mark.parametrize("source", ["edit", "regenerate"])
async def test_history_change_while_disabled_cannot_revive_old_goal(
    db: AsyncSession,
    client: AsyncClient,
    conversation: Conversation,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    await _goal(client, conversation)
    monkeypatch.setattr(settings, "conversation_goal_org_ids", frozenset())
    replacement = await _start(db, conversation, source=source)
    await _finish(db, replacement)
    monkeypatch.setattr(settings, "conversation_goal_org_ids", frozenset({TEST_ORG_ID}))
    readback = (await client.get(_url(conversation))).json()["goal"]
    assert (readback["status"], readback["blocked_reason"]) == ("paused", "history_changed")
    assert await db.scalar(select(GoalAttempt.id)) is None


async def test_replayed_terminal_reserves_one_input_and_history_change_revokes_it(
    db: AsyncSession, client: AsyncClient, conversation: Conversation
) -> None:
    await _goal(client, conversation)
    run = await _start(db, conversation)
    await _settle(db, conversation, run)
    assert await db.scalar(select(ConversationRunInput.id)) is None
    await _finish(db, run)
    await _settle(db, conversation, run)
    await _settle(db, conversation, run)
    queued = (await db.scalars(select(ConversationRunInput))).one()
    attempt = (await db.scalars(select(GoalAttempt))).one()
    assert (attempt.followup_ordinal, attempt.followup_input_id) == (1, queued.id)
    assert queued.source == "goal_followup" and await _events(db) == []
    assert await goals.verify_goal_followup(db, conversation=conversation, input_id=queued.id)
    await goals.pause_goal_for_history_change(db, conversation)
    await db.commit()
    await db.refresh(queued)
    assert queued.status == "canceled"
    assert not await goals.verify_goal_followup(db, conversation=conversation, input_id=queued.id)
    assert (await client.get(_url(conversation))).json()["goal"][
        "blocked_reason"
    ] == "history_changed"


@pytest.mark.parametrize("new_source", ["chat", "resume"])
async def test_newer_accepted_run_precedes_late_terminal_evaluation(
    db: AsyncSession, client: AsyncClient, conversation: Conversation, new_source: str
) -> None:
    await _goal(client, conversation)
    older = await _start(db, conversation)
    await _finish(db, older, "interrupted" if new_source == "resume" else "completed")
    newer = await _start(
        db, conversation, source=new_source, parent=older if new_source == "resume" else None
    )
    await _settle(db, conversation, older)
    assert (
        await db.scalar(select(GoalAttempt.status).where(GoalAttempt.run_id == older.id))
        == "superseded"
    )
    assert (
        await db.scalar(select(GoalAttempt.status).where(GoalAttempt.run_id == newer.id))
        == "pending"
    )
    assert await _events(db) == []
    assert await db.scalar(select(ConversationRunInput.id)) is None
    assert (await client.get(_url(conversation))).json()["goal"]["status"] == "active"


async def test_queued_user_input_precedes_automatic_continuation(
    db: AsyncSession, client: AsyncClient, conversation: Conversation
) -> None:
    await _goal(client, conversation)
    run = await _start(db, conversation)
    db.add(
        ConversationRunInput(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            user_id=conversation.user_id,
            client_request_id="real-user-input",
            source="chat",
            status="pending",
            position=1,
            input_payload={"messages": [{"role": "user", "content": "Pause here"}]},
        )
    )
    await _finish(db, run)
    await _settle(db, conversation, run)
    assert list(await db.scalars(select(ConversationRunInput.source))) == ["chat"]
    assert await db.scalar(select(GoalAttempt.status)) == "superseded"
    assert (await client.get(_url(conversation))).json()["goal"]["auto_followups"] == 0


@pytest.mark.parametrize(
    ("barrier", "reason"), [("approval", "approval_pending"), ("quota", "followup_limit")]
)
async def test_resume_rechecks_open_approval_and_remaining_allowance(
    db: AsyncSession, client: AsyncClient, conversation: Conversation, barrier: str, reason: str
) -> None:
    await _goal(client, conversation)
    url = _url(conversation)
    if barrier == "approval":
        run = await _start(db, conversation)
        await _finish(db, run, "interrupted")
        await _settle(db, conversation, run)
        assert [e.kind for e in await _events(db)] == ["approval_requested"]
    else:
        assert (
            await client.post(f"{url}/actions", json={"expected_revision": 1, "action": "pause"})
        ).status_code == 200
        row = await goals.get_goal(db, conversation)
        assert row
        row.auto_followups = goals.MAX_FOLLOWUPS
        await db.commit()
    revision = (await client.get(url)).json()["goal"]["revision"]
    refused = await client.post(
        f"{url}/actions", json={"expected_revision": revision, "action": "resume"}
    )
    assert refused.status_code == 409 and reason in refused.text
    assert (await client.get(url)).json()["goal"]["revision"] == revision
    assert await db.scalar(select(ConversationRunInput.id)) is None


async def test_completed_goal_pins_verified_version_after_artifact_rewrite(
    db: AsyncSession,
    client: AsyncClient,
    conversation: Conversation,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _goal(client, conversation)
    run = await _start(db, conversation)
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "objects"))
    output_dir = tmp_path / "results"
    recorder = await _recorder(conversation, run, output_dir)
    original = b"company,price\nA,100\n"
    (output_dir / "comparison.csv").write_bytes(original)
    assert await recorder.collect_after_tool_result(tool_name="write_file", tool_call_id="file1")
    await _finish(db, run)
    await _settle(db, conversation, run)
    await _settle(db, conversation, run)
    goal = (await client.get(_url(conversation))).json()["goal"]
    assert goal["status"] == "completed"
    evidence = goal["last_evidence"]["checks"][0]
    assert evidence["passed"] and evidence["version_number"] == 1
    events = await _events(db)
    assert len(events) == 1 and events[0].kind == "completed"
    assert events[0].payload["evidence"]["checks"][0]["version_id"] == evidence["version_id"]
    (output_dir / "comparison.csv").write_bytes(b"company,price\nB,3000\n")
    assert await recorder.collect_after_tool_result(tool_name="edit_file", tool_call_id="file2")
    artifact_url = f"{_url(conversation)}/artifacts/{evidence['artifact_id']}"
    url = f"{artifact_url}/versions/{evidence['version_id']}/download"
    downloaded = await client.get(url)
    assert downloaded.status_code == 200 and downloaded.content == original
    assert (await client.get(url.replace(evidence["version_id"], str(uuid4())))).status_code == 404


async def _act(client: AsyncClient, conversation: Conversation, action: str) -> dict:
    url = _url(conversation)
    goal = (await client.get(url)).json()["goal"]
    response = await client.post(
        f"{url}/actions", json={"expected_revision": goal["revision"], "action": action}
    )
    assert response.status_code == 200, response.text
    return response.json()["goal"]


@pytest.mark.parametrize("initial_status", ["active", "paused"])
async def test_resume_on_different_branch_excludes_prior_partial_evidence(
    db: AsyncSession,
    client: AsyncClient,
    conversation: Conversation,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    initial_status: str,
) -> None:
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "objects"))
    body = _body()
    body["criteria"] = [CONDITION, {"kind": "nonempty_file", "logical_path": "sources.txt"}]
    assert (await client.put(_url(conversation), json=body)).status_code == 200
    run = await _start(db, conversation)
    first_dir = tmp_path / "prior"
    recorder = await _recorder(conversation, run, first_dir)
    (first_dir / "comparison.csv").write_bytes(b"company,price\nA,100\n")
    assert await recorder.collect_after_tool_result(tool_name="write_file", tool_call_id="prior")
    await _finish(db, run)
    await _settle(db, conversation, run)
    old = (await client.get(_url(conversation))).json()["goal"]
    assert old["last_evidence"]["progress"] == 1
    if initial_status == "paused":
        assert (await _act(client, conversation, "pause"))["status"] == "paused"

    await set_active_branch(
        db,
        ConversationBranchMutation(
            conversation.id, unit_context(TEST_USER_ID), "another-checkpoint"
        ),
    )
    await db.commit()
    switched = (await client.get(_url(conversation))).json()["goal"]
    assert (switched["version"], switched["status"], switched["last_evidence"]) == (
        old["version"] + 1,
        "paused",
        None,
    )
    await _act(client, conversation, "resume")
    claim = await claim_next_input(
        db, conversation_id=conversation.id, context=unit_context(TEST_USER_ID)
    )
    assert claim
    await db.commit()

    second_dir = tmp_path / "other-branch"
    fresh = await _recorder(conversation, claim.run, second_dir)
    (second_dir / "sources.txt").write_bytes(b"reference on current branch")
    assert await fresh.collect_after_tool_result(tool_name="write_file", tool_call_id="current")
    await _finish(db, claim.run)
    await _settle(db, conversation, claim.run)
    current = (await client.get(_url(conversation))).json()["goal"]
    checks = current["last_evidence"]["checks"]
    assert current["status"] == "active" and [check["passed"] for check in checks] == [False, True]
    assert all(event.kind != "completed" for event in await _events(db))


@pytest.mark.parametrize("ordinal", [1, goals.MAX_FOLLOWUPS])
async def test_repeated_resume_rearms_the_same_unclaimed_input_at_its_original_ordinal(
    db: AsyncSession, client: AsyncClient, conversation: Conversation, ordinal: int
) -> None:
    await _goal(client, conversation)
    run = await _start(db, conversation)
    goal = await goals.get_goal(db, conversation)
    assert goal
    goal.auto_followups = ordinal - 1
    await _finish(db, run)
    await _settle(db, conversation, run)
    queued = (await db.scalars(select(ConversationRunInput))).one()
    original_input = queued.id
    original_revision = queued.revision
    paused = await _act(client, conversation, "pause")
    await db.refresh(queued)
    assert queued.status == "canceled"
    body = {"action": "resume", "expected_revision": paused["revision"]}
    url = f"{_url(conversation)}/actions"
    assert (await client.post(url, json=body)).status_code == 200
    assert (await client.post(url, json=body)).status_code == 409
    await db.refresh(queued)
    assert (queued.id, queued.status, queued.revision) == (
        original_input,
        "pending",
        original_revision + 2,
    )
    assert list(await db.scalars(select(ConversationRunInput.id))) == [original_input]
    receipt = (await db.scalars(select(GoalAttempt))).one()
    assert receipt.followup_ordinal == ordinal and receipt.followup_input_id == original_input
    assert (await client.get(_url(conversation))).json()["goal"]["auto_followups"] == ordinal


@pytest.mark.parametrize("newer_while_paused", [False, True])
async def test_resume_allocates_one_fresh_run_for_latest_ordinary_terminal(
    db: AsyncSession, client: AsyncClient, conversation: Conversation, newer_while_paused: bool
) -> None:
    await _goal(client, conversation)
    predecessor = await _start(db, conversation)
    if newer_while_paused:
        await _act(client, conversation, "pause")
    await _finish(db, predecessor, "failed")
    await _settle(db, conversation, predecessor)
    if newer_while_paused:
        latest = await _start(db, conversation)
        await _finish(db, latest)
    else:
        latest = predecessor
        assert (await client.get(_url(conversation))).json()["goal"][
            "blocked_reason"
        ] == "run_failed"
    resumed = await _act(client, conversation, "resume")
    assert (resumed["status"], resumed["auto_followups"]) == ("active", 1)
    queued = (await db.scalars(select(ConversationRunInput))).one()
    receipt = (
        await db.scalars(select(GoalAttempt).where(GoalAttempt.followup_input_id == queued.id))
    ).one()
    assert receipt.run_id == latest.id and receipt.requires_fresh_run
    assert queued.status == "pending" and receipt.followup_ordinal == 1
