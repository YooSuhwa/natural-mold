"""Branch-scoped snapshot restoration from live root checkpoints."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from deepagents.graph import DeepAgentState
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite

from app.exceptions import ConflictError, NotFoundError
from app.services import ag_ui_snapshot_history as history


def _checkpoint(
    cid: str,
    parent: str | None,
    messages: list[BaseMessage],
    *,
    namespace: str = "",
    thread: str = "thread",
) -> SimpleNamespace:
    config = {"checkpoint_id": cid, "thread_id": thread, "checkpoint_ns": namespace}
    return SimpleNamespace(
        config={"configurable": config},
        parent_config={"configurable": {**config, "checkpoint_id": parent}} if parent else None,
        checkpoint={"channel_values": {"messages": messages}},
        pending_writes=[("task", "messages", [AIMessage(content="uncommitted")])],
    )


class _Saver:
    def __init__(self, *records: SimpleNamespace) -> None:
        self.records = records
        self.reads = 0

    async def alist(self, config: dict[str, Any]) -> AsyncIterator[SimpleNamespace]:
        self.reads += 1
        assert config == {"configurable": {"thread_id": "thread", "checkpoint_ns": ""}}
        for record in self.records:
            yield record


@pytest.mark.asyncio
async def test_explicit_ancestor_chooses_its_leaf_and_preserves_edit_checkpoints() -> None:
    human = HumanMessage(id="same", content="original")
    edited = HumanMessage(id="same", content="edited")
    assistant = AIMessage(id="answer", content="reply")
    saver = _Saver(
        _checkpoint("child", None, [AIMessage(content="child private")], namespace="child:1"),
        _checkpoint("foreign", None, [AIMessage(content="foreign")], thread="another"),
        _checkpoint("new-tip", "base", [edited]),
        _checkpoint("old-tip", "old-start", [human, assistant]),
        _checkpoint("old-start", "base", [human]),
        _checkpoint("base", None, []),
    )
    result = await history._live_history(saver, "thread", "old-start")
    assert result.checkpoint_id == "old-tip"
    assert result.lineage_checkpoint_ids == ("base", "old-start", "old-tip")
    assert result.has_branches
    assert [
        (r.original_index, r.message, r.checkpoint_id, r.parent_checkpoint_id)
        for r in result.messages
    ] == [(0, human, "old-start", "base"), (1, assistant, "old-tip", "old-start")]
    assert result.messages[0].sibling_checkpoint_ids == ("old-tip", "new-tip")
    assert result.messages[1].sibling_checkpoint_ids == ()
    assert saver.reads == 1
    assert (await history._live_history(saver, "thread", None)).messages[0].message == edited


@pytest.mark.parametrize(
    ("parents", "selected"),
    [
        ({"other": None}, "missing"),
        ({"truncated": "missing", "healthy": None}, "truncated"),
        ({"a": "b", "b": "a"}, "a"),
        ({}, "previous-head"),
    ],
)
def test_damaged_or_missing_selected_branch_conflicts(
    parents: dict[str, str | None], selected: str
) -> None:
    with pytest.raises(ConflictError, match="Selected branch"):
        history._select_lineage(parents, selected)


@pytest.mark.asyncio
async def test_empty_unbranched_conversation_can_use_legacy_source() -> None:
    assert await history._live_history(_Saver(), "thread", None) == history.SnapshotHistory(
        None, (), "legacy"
    )


@pytest.mark.asyncio
async def test_native_delta_history_matches_langgraph_after_fork_removal_and_same_id_edit() -> None:
    def reply(state: DeepAgentState) -> dict[str, Any]:
        question = state["messages"][-1]
        return {"messages": [AIMessage(id="reply", content=f"answer {question.content}")]}

    saver = InMemorySaver()
    graph = (
        StateGraph(DeepAgentState)
        .add_node("reply", reply)
        .add_edge(START, "reply")
        .add_edge("reply", END)
        .compile(checkpointer=saver)
    )
    config: RunnableConfig = {"configurable": {"thread_id": "native"}}
    human = HumanMessage(id="prompt", content="first")
    await graph.ainvoke({"messages": [human]}, config)
    before = await graph.aget_state(config)
    original = before.config.get("configurable", {})["checkpoint_id"]
    base = before.parent_config
    assert base
    await graph.ainvoke(
        cast(
            DeepAgentState,
            {"messages": Overwrite(value=[HumanMessage(id="prompt", content="edited")])},
        ),
        base,
    )
    edited = await graph.aget_state(config)
    result = await history._live_history(saver, "native", original)
    assert [r.message for r in result.messages] == before.values["messages"]
    assert result.messages[0].checkpoint_id != original
    assert result.messages[1].checkpoint_id == original
    assert result.messages[0].sibling_checkpoint_ids
    latest = await history._live_history(saver, "native", None)
    assert [r.message for r in latest.messages] == edited.values["messages"]

    await graph.aupdate_state(
        config,
        {"messages": [RemoveMessage(id="prompt"), AIMessage(id="reply", content="replacement")]},
        as_node="reply",
    )
    after = await graph.aget_state(config)
    replay = await history._live_history(saver, "native", None)
    assert (
        [r.message for r in replay.messages]
        == after.values["messages"]
        == [AIMessage(id="reply", content="replacement")]
    )


@pytest.mark.asyncio
async def test_owner_gate_precedes_saver_and_legacy_access(monkeypatch: pytest.MonkeyPatch) -> None:
    denied = NotFoundError("CONVERSATION_NOT_FOUND", "Conversation not found")
    monkeypatch.setattr(history, "require_private_conversation", AsyncMock(side_effect=denied))
    saver = AsyncMock()
    monkeypatch.setattr(history, "loaded_conversation_checkpointer", saver)
    with pytest.raises(NotFoundError):
        await history.load_snapshot_history(AsyncMock(), AsyncMock(id=uuid.uuid4()), AsyncMock())
    saver.assert_not_awaited()


@pytest.mark.asyncio
async def test_root_run_provenance_is_branch_and_tenant_scoped() -> None:
    tenant, prefix_owner, selected_owner, other_owner = [uuid.uuid4() for _ in range(4)]
    shared = _checkpoint("base", None, [HumanMessage(content="prompt")])
    selected = _checkpoint("old", "base", [AIMessage(content="old")])
    inactive = _checkpoint("new", "base", [AIMessage(content="new")])
    for row, owner in [(shared, prefix_owner), (selected, selected_owner), (inactive, other_owner)]:
        row.metadata = {"moldy_tenant_id": str(tenant), "moldy_run_id": str(owner)}
    records = _Saver(inactive, selected, shared)
    result = await history._live_history(records, "thread", "old", tenant_id=tenant)
    assert result.checkpoint_run_ids == {"base": prefix_owner, "old": selected_owner}
    assert result.other_branch_run_ids == {other_owner}
    inactive.metadata["moldy_tenant_id"] = str(uuid.uuid4())
    with pytest.raises(ConflictError):
        await history._live_history(records, "thread", "new", tenant_id=tenant)


def test_removed_message_preserves_provenance_of_remaining_stable_id() -> None:
    older = HumanMessage(id="old", content="old")
    keep = AIMessage(id="keep", content="keep")
    before = history._replace_snapshot([], [older, keep], "one", None)
    after = history._replace_snapshot(before, [keep], "two", "one")
    assert after == [history.SnapshotMessageRef(keep, 0, "one", None)]


@pytest.mark.asyncio
async def test_unavailable_saver_cannot_verify_unbranched_legacy_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation = AsyncMock(
        id=uuid.uuid4(), agent_lifecycle_state="active", active_branch_checkpoint_id=None
    )
    monkeypatch.setattr(
        history, "require_private_conversation", AsyncMock(return_value=conversation)
    )
    monkeypatch.setattr(
        history,
        "loaded_conversation_checkpointer",
        AsyncMock(
            side_effect=RuntimeError(
                "Checkpointer not initialized. Call init_checkpointer() first."
            )
        ),
    )
    with pytest.raises(ConflictError):
        await history.load_snapshot_history(AsyncMock(), conversation, AsyncMock())


def test_duplicate_id_and_anonymous_moves_cannot_prove_run_ownership() -> None:
    first = HumanMessage(id="dup", content="one")
    second = HumanMessage(id="dup", content="two")
    anonymous = AIMessage(content="anonymous")
    before = history._replace_snapshot([], [first, second, anonymous], "one", None)
    after = history._replace_snapshot(before, [anonymous, second], "two", "one")
    assert all(ref.checkpoint_id is None for ref in after)


@pytest.mark.parametrize(
    "encoded",
    [
        lambda items: {"__overwrite__": items},
        lambda items: {"value": items, "type": "__overwrite__"},
    ],
)
@pytest.mark.asyncio
async def test_serialized_overwrite_and_nested_legacy_batch(encoded: Any) -> None:
    before = _checkpoint("before", None, [HumanMessage(content="discard")])
    after = _checkpoint("after", "before", [])
    after.checkpoint = {"channel_values": {}, "channel_versions": {"messages": "02"}}
    before.pending_writes = [
        ("update", "messages", encoded([[{"role": "user", "id": "new", "content": "edited"}]])),
        ("answer", "messages", [[AIMessage(content="answer")]]),
    ]
    result = await history._live_history(_Saver(after, before), "thread", "after")
    assert [ref.message for ref in result.messages] == [
        HumanMessage(id="new", content="edited"),
        AIMessage(content="answer"),
    ]


@pytest.mark.asyncio
async def test_todos_only_from_selected_leaf_with_valid_public_fields_and_redaction() -> None:
    secret = "test-token-secret-for-visible-state"
    root = _checkpoint("base", None, [])
    visible = _checkpoint("chosen", "base", [])
    hidden = _checkpoint("inactive", "base", [])
    child = _checkpoint("child", None, [], namespace="child:1")
    for item in (hidden, child):
        item.checkpoint["channel_values"]["todos"] = [{"content": "private", "status": "pending"}]
    visible.checkpoint["channel_values"]["todos"] = [
        {"content": f"verify {secret}", "status": "completed", "internal_token": "never"}
    ]
    result = await history._live_history(
        _Saver(child, hidden, visible, root), "thread", "chosen", secret_values=(secret,)
    )
    (todo,) = result.todos
    assert set(todo) == {"id", "content", "status"} and todo["id"] == "todo-1"
    assert todo["status"] == "completed" and "verify" in todo["content"]
    assert secret not in str(result.todos) and "private" not in str(result.todos)


@pytest.mark.asyncio
async def test_changed_channel_version_without_durable_writes_conflicts() -> None:
    older = _checkpoint("older", None, [HumanMessage(content="superseded")])
    newer = _checkpoint("newer", "older", [])
    older.checkpoint["channel_versions"] = {"messages": "1"}
    older.pending_writes = []
    newer.checkpoint = {"channel_values": {}, "channel_versions": {"messages": "2"}}
    with pytest.raises(ConflictError):
        await history._live_history(_Saver(newer, older), "thread", "newer")
