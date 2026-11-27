"""Public chat readers recover a durable run at a precise frame without triggering execution."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer
from app.agent_runtime.langgraph_lifecycle_events import lifecycle_protocol_event
from app.agent_runtime.protocol_events import stored_protocol_event
from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.auth.jwt import create_access_token
from app.config import settings
from app.models.ag_ui_command_receipt import AgUiCommandReceipt
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.models.conversation_run_input import ConversationRunInput
from app.models.message_event import MessageEventChunk
from app.models.organization import Organization, OrganizationMember, Tenant, TenantMember
from app.services import conversation_run_service, trace_storage
from tests.authz.unit_scope_fixture import TEST_ORG_ID, TEST_TENANT_ID
from tests.conftest import TEST_USER_ID
from tests.conversation_replay_retained_fixture import LegacyReplaySeed, seed_legacy_purged_replay
from tests.test_conversation_run_message_links import _create_run, _seed_conversation

pytestmark = pytest.mark.asyncio


@dataclass(frozen=True)
class Chat:
    id: UUID
    run_id: UUID

    @property
    def url(self) -> str:
        return f"/api/conversations/{self.id}/ag-ui"


def _sign_in(client: AsyncClient, org_id: UUID = TEST_ORG_ID) -> None:
    client.cookies.set(settings.cookie_name_access, create_access_token(TEST_USER_ID))
    client.cookies.set(ACTIVE_ORG_COOKIE, str(org_id))


async def _seed(db: AsyncSession, client: AsyncClient, *, finished: bool = True) -> Chat:
    agent, conversation = await _seed_conversation(db)
    run = await _create_run(db, conversation=conversation, agent=agent)
    await conversation_run_service.transition_run(db, run, "running", worker_instance_id="worker-a")
    for index, text in enumerate(("one", "two"), start=1):
        event = stored_protocol_event(
            id=f"source-{index}",
            seq=index * 10,  # Framework event numbers can legally jump.
            run_id=str(run.id),
            thread_id=str(conversation.id),
            method="messages",
            data={"id": f"reply-{index}", "type": "ai", "content": text},
        )
        await trace_storage.append_events(
            db,
            conversation_id=conversation.id,
            assistant_msg_id=str(run.id),
            events_chunk=[dict(event)],
        )
        await db.flush()
    if finished:
        completed = lifecycle_protocol_event(
            run_id=str(run.id), thread_id=str(conversation.id), seq=30, event="completed"
        )
        await trace_storage.append_events(
            db,
            conversation_id=conversation.id,
            assistant_msg_id=str(run.id),
            events_chunk=[dict(completed)],
        )
        await trace_storage.finalize_turn(db, assistant_msg_id=str(run.id), status="completed")
        await conversation_run_service.transition_run(db, run, "completed")
    else:
        run.heartbeat_at = utc_now_naive() - timedelta(days=1)
    await db.commit()
    _sign_in(client)
    return Chat(conversation.id, run.id)


def _sse(body: str) -> list[dict[str, Any]]:
    records = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(":", 1) for line in block.splitlines() if ":" in line)
        if "data" in fields:
            records.append({"event": json.loads(fields["data"]), "id": fields["id"].strip()})
    return records


async def test_completed_chat_prefix_and_every_subframe_resume_exactly_once(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    chat = await _seed(db, raw_client)
    params = {"runId": str(chat.run_id)}
    fresh = await raw_client.get(f"{chat.url}/stream", params=params)
    alias = await raw_client.get(f"{chat.url}/runs/{chat.run_id}/stream")
    assert fresh.status_code == alias.status_code == 200
    full = _sse(fresh.text)
    assert _sse(alias.text) == full
    assert full[0]["event"]["type"] == "RUN_STARTED"
    assert full[-1]["event"]["type"] == "RUN_FINISHED"
    assert [
        (r["event"]["messageId"], r["event"]["delta"])
        for r in full
        if r["event"]["type"] == "TEXT_MESSAGE_CONTENT"
    ] == [("reply-1", "one"), ("reply-2", "two")]
    assert len({r["id"] for r in full}) == len(full)
    mid_message = next(r["id"] for r in full if r["event"]["type"] == "TEXT_MESSAGE_CONTENT")
    for url_params, headers in (
        (params, {"Last-Event-ID": mid_message}),
        ({**params, "after": mid_message}, {}),
    ):
        resumed = await raw_client.get(f"{chat.url}/stream", params=url_params, headers=headers)
        assert resumed.status_code == 200
        assert _sse(resumed.text) == full

    # The underlying journal has three source rows. Splitting after *each* frame also
    # reconnects between TEXT_MESSAGE_START and CONTENT from the same source.
    page_params = {**params, "limit": "1"}
    for remaining, expected in enumerate(full, start=1):
        response = await raw_client.get(f"{chat.url}/events", params=page_params)
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["frames"] == [expected]
        assert page["cursor"] == expected["id"]
        assert page["hasMore"] is (remaining < len(full))
        page_params["after"] = page["cursor"]
    exhausted = await raw_client.get(f"{chat.url}/events", params=page_params)
    assert exhausted.status_code == 200
    assert exhausted.json()["frames"] == [] and exhausted.json()["terminal"] is True
    assert await db.scalar(select(func.count()).select_from(ConversationRun)) == 1
    assert await db.scalar(select(func.count()).select_from(ConversationRunInput)) == 0


@pytest.mark.parametrize("invalidate", ["branch", "storage-gap", "replaced", "unknown"])
async def test_invalid_cursor_requires_snapshot_without_silently_skipping_or_reexecuting(
    raw_client: AsyncClient, db: AsyncSession, invalidate: str
) -> None:
    chat = await _seed(db, raw_client)
    params = {"runId": str(chat.run_id)}
    initial = await raw_client.get(f"{chat.url}/events", params=params)
    assert initial.status_code == 200, initial.text
    records = initial.json()["frames"]
    cursor = next(r["id"] for r in records if r["event"]["type"] == "TEXT_MESSAGE_CONTENT")
    if invalidate == "branch":
        conversation = await db.get(Conversation, chat.id)
        assert conversation
        conversation.active_branch_checkpoint_id = "another-selected-branch"
        await db.commit()
    elif invalidate == "storage-gap":
        await db.execute(
            delete(MessageEventChunk).where(
                MessageEventChunk.assistant_msg_id == str(chat.run_id),
                MessageEventChunk.seq_start == 1,
            )
        )
        await db.commit()
    elif invalidate == "replaced":
        chunk = await db.scalar(
            select(MessageEventChunk).where(
                MessageEventChunk.assistant_msg_id == str(chat.run_id),
                MessageEventChunk.seq_start == 1,
            )
        )
        assert chunk
        source = dict(chunk.events[0])
        source["data"] = {"id": "reply-1", "type": "ai", "content": "changed"}
        chunk.events = [source]
        await db.commit()
    else:
        cursor = "cursor-from-another-format"
    response = await raw_client.get(f"{chat.url}/events", params={**params, "after": cursor})
    assert response.status_code == 409, response.text
    assert "AG_UI_SNAPSHOT_REQUIRED" in response.text
    assert "reply-2" not in response.text
    interrupted = await raw_client.get(f"{chat.url}/stream", params={**params, "after": cursor})
    assert interrupted.status_code == 409, interrupted.text
    assert "AG_UI_SNAPSHOT_REQUIRED" in interrupted.text
    assert await db.scalar(select(func.count()).select_from(ConversationRun)) == 1


async def test_missing_final_source_after_two_committed_answers_requires_snapshot(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    chat = await _seed(db, raw_client)
    params = {"runId": str(chat.run_id)}
    full = await raw_client.get(f"{chat.url}/events", params=params)
    assert full.status_code == 200, full.text
    assert full.json()["frames"][-1]["event"]["type"] == "RUN_FINISHED"
    # Simulate truncating just the last committed journal source. Status is
    # completed and the two remaining message sources have contiguous ordinals.
    chunk = MessageEventChunk
    await db.execute(
        delete(chunk).where(chunk.assistant_msg_id == str(chat.run_id), chunk.seq_start == 3)
    )
    await db.commit()
    remaining = await db.scalars(
        select(chunk.seq_start)
        .where(chunk.assistant_msg_id == str(chat.run_id))
        .order_by(chunk.seq_start)
    )
    assert list(remaining) == [1, 2]
    run = await db.get(ConversationRun, chat.run_id)
    assert run is not None and run.status == "completed"
    for path in ("events", "stream"):
        response = await raw_client.get(f"{chat.url}/{path}", params=params)
        assert response.status_code == 409, response.text
        assert "AG_UI_SNAPSHOT_REQUIRED" in response.text
        assert "RUN_FINISHED" not in response.text


async def test_reads_of_a_silent_worker_leave_stale_decisions_to_the_worker(
    raw_client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ASGITransport skips lifespan. Supply a real empty saver behind the usual scope gate.
    monkeypatch.setattr(checkpointer, "_checkpointer", InMemorySaver())
    chat = await _seed(db, raw_client, finished=False)
    params = {"runId": str(chat.run_id)}
    run = await db.get(ConversationRun, chat.run_id)
    assert run
    heartbeat = run.heartbeat_at
    mutations: list[str] = []

    def capture(*, statement: str, **_: object) -> None:
        verb = statement.strip().split(maxsplit=1)[0].upper()
        if verb in {"DELETE", "INSERT", "UPDATE", "REPLACE"}:
            mutations.append(verb)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", capture, named=True)
    try:
        for _ in range(2):
            page = await raw_client.get(f"{chat.url}/events", params=params)
            snapshot = await raw_client.get(f"{chat.url}/snapshot", params=params)
            assert page.status_code == snapshot.status_code == 200, snapshot.text
            assert page.json()["terminal"] is False
            assert snapshot.json()["activeRunId"] == str(chat.run_id)
            assert snapshot.json()["physicalRunId"] == str(chat.run_id)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not mutations
    await db.refresh(run)
    assert run.status == "running" and run.is_active
    assert run.worker_instance_id == "worker-a" and run.heartbeat_at == heartbeat
    assert run.completed_at is None and run.cancel_requested_at is None
    assert await db.scalar(select(func.count()).select_from(ConversationRun)) == 1
    assert await db.scalar(select(func.count()).select_from(ConversationRunInput)) == 0


async def test_member_of_two_companies_cannot_use_other_companys_view_to_read_thread(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    chat = await _seed(db, raw_client)
    authorized = await raw_client.get(f"{chat.url}/events", params={"runId": str(chat.run_id)})
    assert authorized.status_code == 200
    other_tenant, other_org = uuid4(), uuid4()
    db.add(
        Tenant(
            id=other_tenant, name="other", slug="other", secrets_namespace=f"tenants/{other_tenant}"
        )
    )
    await db.flush()
    db.add(Organization(id=other_org, tenant_id=other_tenant, name="other", slug="other"))
    await db.flush()
    db.add_all(
        [
            TenantMember(tenant_id=other_tenant, user_id=TEST_USER_ID),
            OrganizationMember(org_id=other_org, tenant_id=other_tenant, user_id=TEST_USER_ID),
        ]
    )
    await db.commit()
    _sign_in(raw_client, other_org)
    missing = f"/api/conversations/{uuid4()}/ag-ui"
    for path in ("events", "snapshot", "stream", f"runs/{chat.run_id}/stream"):
        params = {"runId": str(chat.run_id)}
        foreign = await raw_client.get(f"{chat.url}/{path}", params=params)
        absent = await raw_client.get(f"{missing}/{path}", params=params)
        assert foreign.status_code == absent.status_code == 404
        assert foreign.json() == absent.json()
        assert "reply-1" not in foreign.text


async def test_purged_chat_restores_authorized_messages_without_archived_trace(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    conversation_id = uuid4()
    await seed_legacy_purged_replay(
        db,
        LegacyReplaySeed(conversation_id, "archived-run", {"internal": "archived-private-frame"}),
    )
    await db.commit()
    _sign_in(raw_client)
    url = f"/api/conversations/{conversation_id}/ag-ui/snapshot"
    snapshot = await raw_client.get(url)
    assert snapshot.status_code == 200, snapshot.text
    saved = snapshot.json()
    assert [(m["role"], m["content"]) for m in saved["messages"]] == [("user", "[REDACTED]")]
    assert saved["messages"][0]["id"]
    assert saved["activeRunId"] is None
    assert saved["messages"] == (await raw_client.get(url)).json()["messages"]
    assert "archived-private-frame" not in snapshot.text
    assert await db.scalar(select(func.count()).select_from(ConversationRun)) == 0


async def test_public_id_binding_is_stable_and_rejects_physical_alias_and_unsupported_version(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    chat = await _seed(db, raw_client)
    public_id = "client/run ?%+한글"
    receipt = AgUiCommandReceipt(
        tenant_id=TEST_TENANT_ID,
        org_id=TEST_ORG_ID,
        user_id=TEST_USER_ID,
        conversation_id=chat.id,
        public_run_id=public_id,
        run_id=chat.run_id,
        request_digest="0" * 64,
        projection_version="1",
    )
    db.add(receipt)
    await db.commit()
    params = {"runId": public_id}
    authorized = await raw_client.get(f"{chat.url}/stream", params=params)
    assert authorized.status_code == 200, authorized.text
    frames = _sse(authorized.text)
    assert frames[0]["event"]["runId"] == frames[-1]["event"]["runId"] == public_id
    hidden = await raw_client.get(f"{chat.url}/events", params={"runId": str(chat.run_id)})
    assert hidden.status_code == 404
    receipt.projection_version = "future"
    await db.commit()
    rejected = await raw_client.get(
        f"{chat.url}/events", params={**params, "after": frames[0]["id"]}
    )
    assert rejected.status_code == 409, rejected.text
    assert "AG_UI_SNAPSHOT_REQUIRED" in rejected.text
    assert "reply-1" not in rejected.text


async def test_workerless_cancellation_never_opens_a_graph_lifecycle(
    raw_client: AsyncClient, db: AsyncSession
) -> None:
    agent, conversation = await _seed_conversation(db)
    run = await _create_run(db, conversation=conversation, agent=agent)
    await db.commit()
    _sign_in(raw_client)
    url = f"/api/conversations/{conversation.id}/ag-ui"
    params = {"runId": str(run.id)}
    queued = await raw_client.get(f"{url}/events", params=params)
    assert queued.status_code == 200
    assert queued.json()["frames"] == []
    await conversation_run_service.transition_run(db, run, "canceling")
    await conversation_run_service.transition_run(
        db, run, "canceled", allow_workerless_cancellation_ack=True
    )
    await conversation_run_service.finalize_run_outputs_for_status(
        db, run, "canceled", append_terminal_event=True
    )
    await db.commit()

    stream = await raw_client.get(f"{url}/stream", params=params)
    assert stream.status_code == 200
    frames = _sse(stream.text)
    assert [frame["event"]["type"] for frame in frames] == ["RUN_ERROR"]
    assert frames[0]["event"]["code"] == "AG_UI_INPUT_UNAVAILABLE"
    tail = await raw_client.get(f"{url}/events", params={**params, "after": frames[0]["id"]})
    assert tail.status_code == 200
    assert tail.json()["terminal"] and not tail.json()["frames"]
