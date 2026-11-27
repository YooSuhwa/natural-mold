"""Queued chat continues the selected root branch when other branches have newer writes."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from ag_ui.core import RunAgentInput
from fastapi import HTTPException, Request
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from psycopg_pool import PoolTimeout
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.agent_runtime.runtime_config import AgentConfig
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.conversation_run_input import ConversationRunInput
from app.services import conversation_run_queue_service, conversation_run_queue_worker
from app.services.ag_ui_commands import find_or_reserve
from app.services.ag_ui_queue_branch import DEFAULT_BRANCH_AT_CLAIM
from app.services.ag_ui_retry import load_retry_start
from app.services.ag_ui_start import accept_start
from app.services.conversation_run_service import transition_run
from app.tenancy.checkpoint_audit import checkpoint_tenant
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent

OWNER = unit_context(TEST_USER_ID)


@pytest.fixture
def saver(monkeypatch: pytest.MonkeyPatch) -> InMemorySaver:
    memory = InMemorySaver()
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: memory)
    return memory


def _checkpoint_id(config: RunnableConfig) -> str:
    value = config.get("configurable", {}).get("checkpoint_id")
    assert isinstance(value, str)
    return value


async def _enqueue(
    db: AsyncSession,
    conversation_id: UUID,
    text: str,
    *,
    ag_ui: bool = True,
    checkpoint_id: str | None = None,
) -> ConversationRunInput:
    if ag_ui:
        wire = RunAgentInput.model_validate(
            {
                "threadId": str(conversation_id),
                "runId": text,
                "messages": [{"id": "from-client", "role": "user", "content": text}],
            }
        )
        digest = hashlib.sha256(wire.model_dump_json(by_alias=True).encode()).hexdigest()
        admission = await find_or_reserve(db, OWNER, conversation_id, text, digest)
        conversation = await db.get(Conversation, conversation_id)
        assert conversation is not None
        binding = await accept_start(
            db=db,
            context=OWNER,
            conversation=conversation,
            admission=admission,
            request=Request({"type": "http", "headers": []}),
            user=CurrentUser(id=OWNER.user_id, email="test@example.test", name="User"),
            run_input=wire,
        )
        assert binding.input_id is not None
        queued = await db.get(ConversationRunInput, binding.input_id)
        assert queued is not None
        return queued
    return await conversation_run_queue_service.enqueue_input(
        db,
        conversation_id=conversation_id,
        context=OWNER,
        client_request_id=text,
        source="chat",
        input_payload={"messages": [{"role": "user", "content": text}]},
        attachment_ids=[],
        checkpoint_id=checkpoint_id,
        priority=0,
    )


@pytest.mark.parametrize("tagged", [True, False], ids=["current", "historic-untagged"])
async def test_fifo_turns_run_on_the_leaf_selected_after_enqueue(
    db: AsyncSession, saver: InMemorySaver, monkeypatch: pytest.MonkeyPatch, tagged: bool
) -> None:
    conversation_id = await seed_conversation_with_agent()
    root: RunnableConfig = {"configurable": {"thread_id": str(conversation_id)}}
    if tagged:
        root["configurable"].update(checkpoint_tenant(OWNER.tenant_id))
    builder = StateGraph(MessagesState)
    builder.add_node("answer", lambda state: {})
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    graph = builder.compile(checkpointer=saver)
    await graph.ainvoke({"messages": [HumanMessage("shared")]}, root)
    trunk = (await graph.aget_state(root)).config
    await graph.ainvoke({"messages": [HumanMessage("selected")]}, trunk)
    selected = _checkpoint_id((await graph.aget_state(root)).config)
    await graph.ainvoke({"messages": [HumanMessage("other branch")]}, trunk)
    other = (await graph.aget_state(root)).config
    assert _checkpoint_id(other) != selected
    # Child graphs have their own checkpoint sequence under the same conversation.
    child = {**root["configurable"], "checkpoint_ns": "task:child"}
    await saver.aput({"configurable": child}, empty_checkpoint(), {}, {})

    first = await _enqueue(db, conversation_id, "first")
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    conversation.active_branch_checkpoint_id = selected
    await db.commit()

    checkpoints: list[str | None] = []
    results: list[list[str]] = []
    pending: list[UUID] = []

    # The real queue owns claim, persisted run identity, authz and config rehydration.
    # Execute its graph input synchronously so each assertion observes a completed turn.
    async def run_graph(
        *, run_id: UUID, cfg: AgentConfig, input_payload: MessagesState, **_: object
    ) -> None:
        config: RunnableConfig = {
            "configurable": {"thread_id": cfg.thread_id, **checkpoint_tenant(OWNER.tenant_id)}
        }
        if cfg.checkpoint_id is not None:
            config["configurable"]["checkpoint_id"] = cfg.checkpoint_id
        async with TestSession() as scope:
            run = await scope.get(ConversationRun, run_id)
            assert run is not None
            await transition_run(scope, run, "running", worker_instance_id="branch-fixture")
            if not checkpoints:
                pending.append((await _enqueue(scope, conversation_id, "second")).id)
            await scope.commit()
        if not checkpoints:
            # A send arriving during the first run cannot claim a position on the old leaf.
            assert (
                await conversation_run_queue_worker.dispatch_next_for_conversation(conversation_id)
                is None
            )
        checkpoints.append(cfg.checkpoint_id)
        values = await graph.ainvoke(input_payload, config)
        results.append([message.content for message in values["messages"]])
        async with TestSession() as scope:
            run = await scope.get(ConversationRun, run_id)
            assert run is not None
            await transition_run(scope, run, "completed")
            await scope.commit()

    monkeypatch.setattr(conversation_run_queue_worker, "start_conversation_run", run_graph)
    first_claim = await conversation_run_queue_worker.dispatch_next_for_conversation(
        conversation_id
    )
    assert first_claim and first_claim.id == first.id
    after_first = _checkpoint_id((await graph.aget_state(root)).config)
    second_claim = await conversation_run_queue_worker.dispatch_next_for_conversation(
        conversation_id
    )
    assert second_claim and [second_claim.id] == pending
    assert checkpoints == [selected, after_first]
    assert results == [["shared", "selected", "first"], ["shared", "selected", "first", "second"]]
    sibling = await graph.aget_state(other)
    assert [message.content for message in sibling.values["messages"]] == ["shared", "other branch"]
    assert await db.scalar(select(func.count(ConversationRun.id))) == 2


@pytest.mark.parametrize("invalid_selection", ["missing", "child", "foreign"])
async def test_unavailable_selected_root_never_launches_on_latest(
    db: AsyncSession, saver: InMemorySaver, invalid_selection: str
) -> None:
    conversation_id = await seed_conversation_with_agent()
    root = {
        "thread_id": str(conversation_id),
        "checkpoint_ns": "",
        **checkpoint_tenant(OWNER.tenant_id),
    }
    await saver.aput({"configurable": root}, empty_checkpoint(), {}, {})
    invalid = empty_checkpoint()
    if invalid_selection == "child":
        await saver.aput({"configurable": {**root, "checkpoint_ns": "task:child"}}, invalid, {}, {})
    if invalid_selection == "foreign":
        await saver.aput({"configurable": {**root, **checkpoint_tenant(uuid4())}}, invalid, {}, {})
    queued = await _enqueue(db, conversation_id, "waiting for branch")
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    conversation.active_branch_checkpoint_id = invalid["id"]
    await db.commit()
    claim = await conversation_run_queue_worker.dispatch_next_for_conversation(conversation_id)
    await db.refresh(queued)
    await db.refresh(conversation)
    assert claim is None and queued.status == "pending" and queued.run_id is None
    assert conversation.queue_paused
    assert await db.scalar(select(func.count(ConversationRun.id))) == 0


async def test_native_chat_keeps_its_existing_checkpoint_contract(db: AsyncSession) -> None:
    conversation_id = await seed_conversation_with_agent()
    queued = await _enqueue(db, conversation_id, "native client", ag_ui=False)
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    conversation.active_branch_checkpoint_id = "legacy-selection"
    await db.commit()
    claim = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=conversation_id, context=OWNER
    )
    assert claim is not None and claim.input.id == queued.id
    assert claim.input.checkpoint_id is None
    assert claim.run.metadata_json and claim.run.metadata_json["checkpoint_id"] is None
    assert DEFAULT_BRANCH_AT_CLAIM not in claim.run.metadata_json


@pytest.mark.parametrize(
    ("receipt", "selected"),
    [(True, "explicit-root"), (False, "explicit-root"), (True, None)],
    ids=["ag-ui-selected", "native-selected", "ag-ui-default"],
)
async def test_older_claim_without_checkpoint_uses_only_the_default_ag_ui_branch(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch, receipt: bool, selected: str | None
) -> None:
    conversation_id = await seed_conversation_with_agent()
    queued = await _enqueue(db, conversation_id, "authoritative text", ag_ui=receipt)
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    conversation.active_branch_checkpoint_id = selected
    await db.commit()
    with monkeypatch.context() as old_worker:
        # Claim from the old binary: all normal locks/creation, no branch capture.
        old_worker.setattr(
            "app.services.conversation_run_queue_claim.pin_selected_queue_head",
            AsyncMock(return_value=True),
        )
        claim = await conversation_run_queue_service.claim_next_input(
            db, conversation_id=conversation_id, context=OWNER
        )
        assert claim and claim.input.id == queued.id and claim.input.checkpoint_id is None
        await db.commit()
    start = AsyncMock()
    cfg = AsyncMock(wraps=conversation_run_queue_worker.resolve_agent_context)
    monkeypatch.setattr(conversation_run_queue_worker, "start_conversation_run", start)
    monkeypatch.setattr(conversation_run_queue_worker, "resolve_agent_context", cfg)
    await conversation_run_queue_worker.launch_claimed_input(queued.id)
    await db.refresh(queued)
    await db.refresh(claim.run)
    if receipt and selected:
        cfg.assert_not_awaited()
        start.assert_not_awaited()
        assert queued.status == claim.run.status == "failed"
        assert claim.run.error_code == "queue_branch_unpinned"
        payload, attachments = await load_retry_start(
            db, context=OWNER, conversation=conversation, input_id=queued.id, public_run_id="retry"
        )
        messages = payload["messages"]
        assert isinstance(messages, list) and len(messages) == 1 and isinstance(messages[0], dict)
        assert messages[0]["content"] == "authoritative text" and attachments == []
    else:
        start.assert_awaited_once()
        assert start.await_args is not None and start.await_args.kwargs["cfg"].checkpoint_id is None
        assert queued.status == "claimed" and claim.run.error_code is None


@pytest.mark.parametrize("marker", [True, "true", 1], ids=["server-boolean", "string", "integer"])
async def test_default_claim_remembers_its_branch_if_the_user_switches_before_launch(
    db: AsyncSession, saver: InMemorySaver, monkeypatch: pytest.MonkeyPatch, marker: object
) -> None:
    conversation_id = await seed_conversation_with_agent()
    root: RunnableConfig = {
        "configurable": {"thread_id": str(conversation_id), **checkpoint_tenant(OWNER.tenant_id)}
    }
    builder = StateGraph(MessagesState)
    builder.add_node("reply", lambda state: {})
    builder.add_edge(START, "reply")
    builder.add_edge("reply", END)
    graph = builder.compile(checkpointer=saver)
    await graph.ainvoke({"messages": [HumanMessage("shared")]}, root)
    fork = (await graph.aget_state(root)).config
    await graph.ainvoke({"messages": [HumanMessage("other branch")]}, fork)
    other = (await graph.aget_state(root)).config
    await graph.ainvoke({"messages": [HumanMessage("default branch")]}, fork)

    queued = await _enqueue(db, conversation_id, "new user turn")
    claim = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=conversation_id, context=OWNER
    )
    assert claim and claim.input.id == queued.id and claim.input.checkpoint_id is None
    assert claim.run.metadata_json and claim.run.metadata_json[DEFAULT_BRANCH_AT_CLAIM] is True
    if marker is not True:
        claim.run.metadata_json = {**claim.run.metadata_json, DEFAULT_BRANCH_AT_CLAIM: marker}
        # Python's 1 == True would otherwise hide the deliberately different JSON type.
        flag_modified(claim.run, "metadata_json")
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    conversation.active_branch_checkpoint_id = _checkpoint_id(other)
    await db.commit()

    invoked: list[list[str]] = []

    async def launch(*, cfg: AgentConfig, input_payload: MessagesState, **_: object) -> None:
        assert cfg.checkpoint_id is None
        result = await graph.ainvoke(input_payload, root)
        invoked.append([message.content for message in result["messages"]])

    monkeypatch.setattr(conversation_run_queue_worker, "start_conversation_run", launch)
    await conversation_run_queue_worker.launch_claimed_input(queued.id)
    if marker is True:
        assert invoked == [["shared", "default branch", "new user turn"]]
        visible = await graph.aget_state(other)
        assert [message.content for message in visible.values["messages"]] == [
            "shared",
            "other branch",
        ]
    else:
        assert not invoked
        await db.refresh(claim.run)
        assert claim.run.status == "failed" and claim.run.error_code == "queue_branch_unpinned"


async def test_saver_outage_keeps_the_input_eligible_for_the_same_claim(
    db: AsyncSession, saver: InMemorySaver, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from app.services import ag_ui_queue_branch

    conversation_id = await seed_conversation_with_agent()
    root: RunnableConfig = {
        "configurable": {"thread_id": str(conversation_id), "checkpoint_ns": ""}
    }
    checkpoint = empty_checkpoint()
    await saver.aput(root, checkpoint, {}, {})
    queued = await _enqueue(db, conversation_id, "wait for saver")
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    conversation.active_branch_checkpoint_id = checkpoint["id"]
    await db.commit()
    with monkeypatch.context() as patch:
        patch.setattr(
            ag_ui_queue_branch, "selected_branch_head", AsyncMock(side_effect=PoolTimeout)
        )
        assert (
            await conversation_run_queue_service.claim_next_input(
                db, conversation_id=conversation_id, context=OWNER
            )
            is None
        )
    await db.commit()
    assert not conversation.queue_paused and queued.status == "pending" and queued.run_id is None
    claimed = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=conversation_id, context=OWNER
    )
    assert (
        claimed
        and claimed.input.id == queued.id
        and claimed.input.checkpoint_id == checkpoint["id"]
    )


@pytest.mark.parametrize("receipt", [True, False], ids=["server-selected", "legacy-explicit"])
async def test_retry_and_duplicate_use_original_queue_request(
    db: AsyncSession, saver: InMemorySaver, receipt: bool
) -> None:
    conversation_id = await seed_conversation_with_agent()
    selected = empty_checkpoint()
    await saver.aput(
        {
            "configurable": {
                "thread_id": str(conversation_id),
                "checkpoint_ns": "",
                **checkpoint_tenant(OWNER.tenant_id),
            }
        },
        selected,
        {},
        {},
    )
    original = None if receipt else selected["id"]
    queued = await _enqueue(
        db, conversation_id, "original user turn", ag_ui=receipt, checkpoint_id=original
    )
    conversation = await db.get(Conversation, conversation_id)
    assert conversation is not None
    conversation.active_branch_checkpoint_id = selected["id"]
    await db.commit()
    claim = await conversation_run_queue_service.claim_next_input(
        db, conversation_id=conversation_id, context=OWNER
    )
    assert claim and claim.input.id == queued.id
    assert claim.input.checkpoint_id == selected["id"]
    assert claim.run.metadata_json and claim.run.metadata_json["checkpoint_id"] == selected["id"]
    await transition_run(db, claim.run, "failed", error_code="test_failure")
    await db.commit()

    async def repeat(checkpoint_id: str | None) -> ConversationRunInput:
        return await conversation_run_queue_service.enqueue_input(
            db,
            conversation_id=conversation_id,
            context=OWNER,
            client_request_id=queued.client_request_id,
            source="chat",
            input_payload=queued.input_payload,
            attachment_ids=[],
            checkpoint_id=checkpoint_id,
            priority=0,
        )

    assert (await repeat(original)).id == queued.id
    with pytest.raises(HTTPException) as conflict:
        await repeat("changed-checkpoint")
    assert conflict.value.status_code == 409
    if receipt:
        payload, attachments = await load_retry_start(
            db,
            context=OWNER,
            conversation=conversation,
            input_id=queued.id,
            public_run_id="new-attempt",
        )
        expected = payload["messages"]
        assert isinstance(expected, list) and len(expected) == 1
        assert isinstance(expected[0], dict) and expected[0]["content"] == "original user turn"
        assert set(payload) == {"messages"} and attachments == []
    else:
        with pytest.raises(HTTPException) as ineligible:
            await load_retry_start(
                db,
                context=OWNER,
                conversation=conversation,
                input_id=queued.id,
                public_run_id="new-attempt",
            )
        assert ineligible.value.status_code == 409
