from __future__ import annotations

import uuid
from typing import Any

import pytest
from deepagents.graph import DeepAgentState
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite

from app.services.ag_ui_fork_checkpoints import load_fork_checkpoint

_TEST_TENANT = uuid.uuid4()


def conversation_graph(saver: InMemorySaver) -> Any:
    graph = StateGraph(DeepAgentState)
    graph.add_node("answer", lambda _: {"messages": [AIMessage(content="answer", id="reply")]})
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)
    return graph.compile(checkpointer=saver)


async def test_fork_matches_the_native_delta_channel_including_deletions_and_overwrite() -> None:
    saver = InMemorySaver()
    graph = conversation_graph(saver)
    thread = str(uuid.uuid4())
    config: RunnableConfig = {"configurable": {"thread_id": thread}}
    await graph.ainvoke({"messages": [HumanMessage(content="original", id="u1")]}, config)
    root = [row async for row in saver.alist(config)][-1].config["configurable"]["checkpoint_id"]
    await graph.aupdate_state(config, {"messages": [HumanMessage(content="edited", id="u1")]})
    await graph.aupdate_state(config, {"messages": [RemoveMessage(id="reply")]})
    current = await graph.aget_state(config)
    head = current.config["configurable"]["checkpoint_id"]
    args = {"thread_id": thread, "tenant_id": _TEST_TENANT, "selected": None, "expected": head}
    assert await load_fork_checkpoint(saver, target=head, operation="edit", **args) == (
        head,
        current.values["messages"],
    )
    assert [m.content for m in current.values["messages"]] == ["edited"]
    assert await load_fork_checkpoint(saver, target=root, operation="edit", **args) == (head, [])
    with pytest.raises(HTTPException, match="existing user turn"):
        await load_fork_checkpoint(saver, target=root, operation="regenerate", **args)

    await graph.aupdate_state(
        config, {"messages": Overwrite([HumanMessage(content="new", id="u2")])}
    )
    head = (await graph.aget_state(config)).config["configurable"]["checkpoint_id"]
    _, entries = await load_fork_checkpoint(
        saver,
        thread_id=thread,
        tenant_id=_TEST_TENANT,
        selected=None,
        target=head,
        expected=head,
        operation="edit",
    )
    assert [entry.id for entry in entries] == ["u2"]


async def test_fork_stays_in_the_selected_lineage_and_rejects_changed_heads() -> None:
    saver = InMemorySaver()
    graph = conversation_graph(saver)
    thread = str(uuid.uuid4())
    config: RunnableConfig = {"configurable": {"thread_id": thread}}
    await graph.ainvoke({"messages": [HumanMessage(content="first", id="u")]}, config)
    old = await graph.aget_state(config)
    old_head = old.config["configurable"]["checkpoint_id"]
    assert old.parent_config
    await graph.ainvoke(
        {"messages": [HumanMessage(content="alternate", id="other")]}, old.parent_config
    )
    new_head = (await graph.aget_state(config)).config["configurable"]["checkpoint_id"]
    assert old_head != new_head
    for selected, target, expected in (
        (old_head, new_head, old_head),
        (new_head, old_head, new_head),
        (None, new_head, old_head),
        ("unknown-selection", new_head, None),
    ):
        with pytest.raises(HTTPException) as caught:
            await load_fork_checkpoint(
                saver,
                thread_id=thread,
                tenant_id=_TEST_TENANT,
                selected=selected,
                target=target,
                expected=expected,
                operation="edit",
            )
        assert caught.value.status_code == 409
    _, result = await load_fork_checkpoint(
        saver,
        thread_id=thread,
        tenant_id=_TEST_TENANT,
        selected=old_head,
        target=old_head,
        expected=old_head,
        operation="regenerate",
    )
    assert [m.content for m in result] == ["first"]


async def test_unresolved_tool_call_and_nested_namespace_are_not_chat_fork_targets() -> None:
    saver = InMemorySaver()
    graph = conversation_graph(saver)
    thread = str(uuid.uuid4())
    config: RunnableConfig = {"configurable": {"thread_id": thread}}
    await graph.ainvoke({"messages": [HumanMessage(content="first", id="u")]}, config)
    await graph.aupdate_state(
        config,
        {
            "messages": [
                AIMessage(
                    content="", id="call", tool_calls=[{"name": "action", "args": {}, "id": "c"}]
                )
            ]
        },
    )
    inflight = (await graph.aget_state(config)).config["configurable"]["checkpoint_id"]
    with pytest.raises(HTTPException, match="unresolved tool call"):
        await load_fork_checkpoint(
            saver,
            thread_id=thread,
            tenant_id=_TEST_TENANT,
            selected=None,
            target=inflight,
            expected=None,
            operation="edit",
        )

    await graph.aupdate_state(
        config, {"messages": [ToolMessage(content="result", tool_call_id="c")]}
    )
    head = (await graph.aget_state(config)).config["configurable"]["checkpoint_id"]
    assert (
        await load_fork_checkpoint(
            saver,
            thread_id=thread,
            tenant_id=_TEST_TENANT,
            selected=None,
            target=head,
            expected=head,
            operation="regenerate",
        )
    )[1][-1].content == "result"
    # Mirror a real checkpoint into a nested namespace with an ID absent from the root.
    row = await saver.aget_tuple(config)
    assert row
    nested_id = str(uuid.uuid4())
    await saver.aput(
        {"configurable": {"thread_id": thread, "checkpoint_ns": "task:child"}},
        {**row.checkpoint, "id": nested_id},
        row.metadata,
        {},
    )
    with pytest.raises(HTTPException) as caught:
        await load_fork_checkpoint(
            saver,
            thread_id=thread,
            tenant_id=_TEST_TENANT,
            selected=None,
            target=nested_id,
            expected=None,
            operation="edit",
        )
    assert caught.value.status_code == 409


@pytest.mark.parametrize(
    ("operation", "omitted"), [("edit", False), ("edit", True), ("regenerate", False)]
)
async def test_committed_fork_rebuilds_just_its_branch_after_reopening_database(
    operation: str,
    omitted: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ag_ui.core import RunAgentInput
    from sqlalchemy import func, select
    from starlette.requests import Request

    from app.agent_runtime.attachment_inputs import FORK_APPEND_COUNT
    from app.dependencies import CurrentUser
    from app.exceptions import NotFoundError
    from app.models.ag_ui_command_receipt import AgUiCommandReceipt
    from app.models.conversation import Conversation
    from app.models.conversation_run import ConversationRun
    from app.models.conversation_run_input import ConversationRunInput
    from app.models.message_attachment import MessageAttachment
    from app.services.ag_ui_commands import find_or_reserve
    from app.services.ag_ui_delivery_envelope import DeliveryIdentity, open_delivery
    from app.services.ag_ui_fork import prepare_fork_delivery
    from app.services.ag_ui_start import accept_start
    from app.services.conversation_agent_context import resolve_agent_context
    from tests.authz.execution_gateway_testkit import seed_graph
    from tests.conftest import TestSession

    saver = InMemorySaver()
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: saver)
    graph = conversation_graph(saver)
    async with TestSession() as db:
        seed = await seed_graph(db, conversations=1)
        ctx = seed.context
        uid = ctx.user_id
        actor = CurrentUser(id=uid, name="Gateway user", email="local@example.com")
        conv_id = seed.conversation_ids[0]
        upload = MessageAttachment(
            user_id=uid,
            tenant_id=ctx.tenant_id,
            org_id=ctx.org_id,
            filename="note.txt",
            mime_type="text/plain",
            storage_path="test/note.txt",
            url="/test/note.txt",
            size_bytes=3,
        )
        db.add(upload)
        await db.commit()
        attachment = upload.id
    graph_cfg: RunnableConfig = {"configurable": {"thread_id": str(conv_id)}}
    await graph.ainvoke(
        {"messages": [HumanMessage(content="original branch", id="old")]}, graph_cfg
    )
    state = await graph.aget_state(graph_cfg)
    head = state.config["configurable"]["checkpoint_id"]
    root = [row async for row in saver.alist(graph_cfg)][-1].config["configurable"]["checkpoint_id"]
    target = root if operation == "edit" else head
    latest = (
        [{"role": "user", "id": "browser-id", "content": "edited turn"}]
        if operation == "edit"
        else []
    )
    if omitted:
        latest = [
            {
                "role": "user",
                "id": "browser-id",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "url", "value": "https://example.invalid/photo"},
                    }
                ],
            }
        ]
    files = [str(attachment)] if operation == "edit" and not omitted else []
    command = RunAgentInput.model_validate(
        {
            "threadId": str(conv_id),
            "runId": "browser-fork",
            "state": {},
            "messages": latest,
            "context": [],
            "tools": [],
            "forwardedProps": {
                "moldy": {
                    "operation": operation,
                    "expectedHead": head,
                    "checkpointId": target,
                    "attachmentIds": files,
                }
            },
        }
    )
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    raw = "9" * 64

    async with TestSession() as db:
        conv = await db.get(Conversation, conv_id)
        assert conv is not None
        admission = await find_or_reserve(db, ctx, conv_id, command.run_id, raw)
        receipt = await accept_start(
            db=db,
            conversation=conv,
            user=actor,
            context=ctx,
            request=request,
            run_input=command,
            admission=admission,
        )
        assert receipt.run_id and receipt.input_id is None
        run_id = receipt.run_id
        await db.commit()
    async with TestSession() as db:
        admission = await find_or_reserve(db, ctx, conv_id, command.run_id, raw)
        assert admission.existing == receipt
        assert await db.scalar(select(func.count(ConversationRun.id))) == 1
        assert await db.scalar(select(func.count(ConversationRunInput.id))) == 0
        encrypted = await db.scalar(select(AgUiCommandReceipt.delivery_encrypted))
        assert encrypted and "edited turn" not in encrypted
        identity = DeliveryIdentity(
            ctx.tenant_id, ctx.org_id, uid, conv_id, command.run_id, run_id, operation, None, target
        )
        private = await open_delivery(db, identity, encrypted)
        assert isinstance(private, dict) and str(private).count("original branch") == 0
        conv = await db.get(Conversation, conv_id)
        run = await db.get(ConversationRun, run_id)
        assert conv and run and run.source == operation and run.status == "queued"
        cfg = await resolve_agent_context(db, conv_id, actor, context=ctx, checkpoint_id=target)
        delivery = {
            "conversation": conv,
            "run": run,
            "context": ctx,
            "user": actor,
            "cfg": cfg,
            "checkpoint_id": target,
            "payload": private,
        }
        native, ids = await prepare_fork_delivery(db, **delivery)
        expected = "edited turn" if operation == "edit" else "original branch"
        assert [m.content for m in native["messages"].value] == ["" if omitted else expected]
        assert native[FORK_APPEND_COUNT] == int(operation == "edit")
        if omitted:
            assert ids == []
            assert native["messages"].value[-1].additional_kwargs == {"moldy_omitted_media": True}
            return
        if operation == "edit":
            assert ids == [attachment]
            assert native["messages"].value[-1].id != "browser-id"
            upload = await db.get(MessageAttachment, attachment)
            assert upload and upload.conversation_id == conv_id
            upload.conversation_id = None
            await db.flush()
            with pytest.raises(NotFoundError) as revoked:
                await prepare_fork_delivery(db, **delivery)
            assert revoked.value.status == 404
        else:
            assert ids == []
            await graph.aupdate_state(
                graph_cfg, {"messages": [HumanMessage(content="replacement", id="u2")]}
            )
            with pytest.raises(HTTPException) as changed:
                await prepare_fork_delivery(db, **delivery)
            assert changed.value.status_code == 409
