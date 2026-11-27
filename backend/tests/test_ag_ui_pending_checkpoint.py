"""Use real persisted graph writes to check what AG-UI reconnect and resume can see."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from operator import add
from typing import Annotated, Any, Protocol, TypedDict
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt, StateSnapshot, interrupt
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.langgraph_event_projection import annotate_session_consent_eligibility
from app.agent_runtime.protocol_events import stored_protocol_event
from app.approvals.service import PendingInterrupt, RunOrigin, record_pending
from app.authz.org_context import OrgContext
from app.exceptions import NotFoundError
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.services.ag_ui_pending_checkpoint import (
    CheckpointUnavailable,
    PendingCheckpoint,
    read_pending_checkpoint,
)
from app.tenancy.checkpoint_audit import run_configurable
from tests.authz.agent_execution_runtime_fixture import seed_runtime_run
from tests.authz.g03_support import approval_payload
from tests.authz.queue_resource_fixture import queue_resource_sql
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import make_user


class Answers(TypedDict):
    answers: Annotated[list[str], add]


class QuestionNode(Protocol):
    def __call__(self, state: Answers) -> Answers: ...


def question(text: str) -> dict[str, Any]:
    return {"type": "ask_user", "question": text, "options": []}


def ask(*questions: dict[str, Any]) -> QuestionNode:
    def node(state: Answers) -> Answers:
        return {"answers": [interrupt(payload) for payload in questions]}

    return node


@dataclass
class PausedChat:
    db: AsyncSession
    run: ConversationRun
    conversation: Conversation
    context: OrgContext
    saver: InMemorySaver

    def graph(self, **nodes: QuestionNode) -> CompiledStateGraph:
        builder = StateGraph(Answers)
        for name, node in nodes.items():
            builder.add_node(name, node).add_edge(START, name).add_edge(name, END)
        return builder.compile(checkpointer=self.saver)

    def config(self) -> RunnableConfig:
        return {
            "configurable": run_configurable(
                str(self.conversation.id), self.run.id, self.context.tenant_id
            )
        }

    async def read(
        self, run: ConversationRun | None = None, *, raise_on_unavailable: bool = False
    ) -> PendingCheckpoint | None:
        return await read_pending_checkpoint(
            self.db,
            conversation=self.conversation,
            context=self.context,
            parent_run=run or self.run,
            raise_on_unavailable=raise_on_unavailable,
        )

    async def set_selection(self, checkpoint_id: str) -> None:
        self.conversation.active_branch_checkpoint_id = checkpoint_id
        await self.db.commit()

    async def set_reported_interrupt(self, interrupt_id: str) -> None:
        self.run.interrupt_id = interrupt_id
        await self.db.commit()

    async def continuation(self, interrupt_id: str) -> ConversationRun:
        run = ConversationRun(
            conversation_id=self.conversation.id,
            agent_id=self.run.agent_id,
            tenant_id=self.context.tenant_id,
            org_id=self.context.org_id,
            user_id=self.context.user_id,
            source="resume",
            parent_run_id=self.run.id,
            status="interrupted",
            interrupt_id=interrupt_id,
        )
        self.db.add(run)
        await self.db.commit()
        return run


@pytest.fixture
async def chat(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> PausedChat:
    seeded, _agent, conversation = await seed_runtime_run()
    run = await db.merge(seeded)
    run.status = "interrupted"
    run.is_active = False
    await db.commit()
    saver = InMemorySaver()
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: saver)
    return PausedChat(db, run, await db.merge(conversation), unit_context(run.user_id), saver)


async def test_parallel_partial_resume_excludes_finished_task_on_same_checkpoint(
    chat: PausedChat,
) -> None:
    graph = chat.graph(left=ask(question("left")), right=ask(question("right")))
    config = chat.config()
    first = await graph.ainvoke({"answers": []}, config)
    left, right = (item.id for item in first["__interrupt__"])
    await chat.set_reported_interrupt(left)
    before = await chat.read()
    assert before is not None
    assert {item["id"] for item in before.interrupts} == {left, right}

    await graph.ainvoke(Command(resume={left: "answered"}), config)
    saved = await chat.saver.aget_tuple(config)
    assert saved is not None
    # The old interrupt still occurs verbatim in the saver's raw writes.
    assert left in {
        item.id
        for _, channel, writes in saved.pending_writes or ()
        if channel == "__interrupt__"
        for item in writes
    }
    with queue_resource_sql(chat.db) as sql:
        after = await chat.read()
    assert sql and all(s.lstrip().upper().startswith(("SELECT", "WITH")) for s in sql)
    assert after is not None and after.checkpoint_id == before.checkpoint_id
    assert [item["id"] for item in after.interrupts] == [right]
    assert after.native_kinds[right] == "ask_user"
    assert await chat.saver.aget_tuple(config) == saved


async def test_propagated_child_interrupt_identifies_its_physical_root_task(
    chat: PausedChat,
) -> None:
    child = StateGraph(Answers)
    child.add_node("inside", ask(question("nested question")))
    child.add_edge(START, "inside").add_edge("inside", END)
    parent = StateGraph(Answers)
    parent.add_node("delegated", child.compile()).add_node("own", ask(question("own question")))
    for name in ("delegated", "own"):
        parent.add_edge(START, name).add_edge(name, END)
    graph = parent.compile(checkpointer=chat.saver)
    config = chat.config()
    result = await graph.ainvoke({"answers": []}, config)
    raw_ids = {slot.value["question"]: slot.id for slot in result["__interrupt__"]}
    await chat.set_reported_interrupt(raw_ids["own question"])
    assert raw_ids["own question"] != raw_ids["nested question"]

    snapshot = await graph.aget_state(config, subgraphs=True)
    tasks = {task.name: task for task in snapshot.tasks}
    delegated, own = tasks["delegated"], tasks["own"]
    nested = delegated.state
    assert isinstance(nested, StateSnapshot)
    (inside,) = nested.tasks
    namespace = nested.config.get("configurable", {})["checkpoint_ns"]
    assert namespace.split("|", 1)[0] == f"delegated:{delegated.id}"
    assert len({delegated.id, own.id, inside.id}) == 3
    assert delegated.interrupts[0].id == inside.interrupts[0].id == raw_ids["nested question"]

    pending = await chat.read()
    assert pending is not None
    assert pending.root_task_ids == {
        raw_ids["own question"]: own.id,
        raw_ids["nested question"]: delegated.id,
    }


async def test_serial_question_rotates_id_and_accepts_only_scoped_resume_lineage(
    chat: PausedChat,
) -> None:
    graph = chat.graph(form=ask(question("first"), question("second")))
    config = chat.config()
    first = await graph.ainvoke({"answers": []}, config)
    first_id = first["__interrupt__"][0].id
    await chat.set_reported_interrupt(first_id)
    original = await chat.read()
    assert original is not None
    second = await graph.ainvoke(Command(resume={first_id: "yes"}), config)
    second_id = second["__interrupt__"][0].id
    assert second_id != first_id
    continuation = await chat.continuation(second_id)
    resumed = await chat.read(continuation)
    assert resumed is not None and resumed.checkpoint_id == original.checkpoint_id
    assert [item["id"] for item in resumed.interrupts] == [second_id]
    assert resumed.interrupts[0]["value"]["action_requests"][0]["args"]["question"] == "second"

    continuation.parent_run_id = None
    await chat.db.commit()
    assert await chat.read(continuation) is None


async def test_non_resume_run_with_predecessor_invalidates_direct_and_inherited_provenance(
    chat: PausedChat,
) -> None:
    graph = chat.graph(form=ask(question("first"), question("second")))
    config = chat.config()
    first = await graph.ainvoke({"answers": []}, config)
    first_id = first["__interrupt__"][0].id
    await chat.set_reported_interrupt(first_id)
    second = await graph.ainvoke(Command(resume={first_id: "answered"}), config)
    second_id = second["__interrupt__"][0].id
    continuation = await chat.continuation(second_id)
    saved = await chat.saver.aget_tuple(config)
    assert saved is not None and saved.metadata.get("moldy_run_id") == str(chat.run.id)
    assert await chat.read() is not None
    assert await chat.read(continuation) is not None

    unrelated = ConversationRun(
        conversation_id=chat.conversation.id,
        agent_id=chat.run.agent_id,
        tenant_id=chat.context.tenant_id,
        org_id=chat.context.org_id,
        user_id=chat.context.user_id,
        source="start",
        status="interrupted",
    )
    chat.db.add(unrelated)
    await chat.db.flush()
    chat.run.parent_run_id = unrelated.id
    await chat.db.commit()
    assert await chat.read() is None
    assert await chat.read(continuation) is None


async def test_selected_ancestor_resolves_to_leaf_but_missing_and_sibling_refuse(
    chat: PausedChat,
) -> None:
    graph = chat.graph(form=ask(question("which branch?")))
    config = chat.config()
    original = await graph.ainvoke({"answers": []}, config)
    interrupt_id = original["__interrupt__"][0].id
    await chat.set_reported_interrupt(interrupt_id)
    leaf = await chat.saver.aget_tuple(config)
    assert leaf is not None and leaf.parent_config is not None
    ancestor = leaf.parent_config.get("configurable", {})["checkpoint_id"]
    await chat.set_selection(ancestor)
    selected = await chat.read()
    assert selected is not None and selected.checkpoint_id == leaf.checkpoint["id"]
    await chat.set_selection(str(uuid4()))
    assert await chat.read(raise_on_unavailable=True) is None

    # Fork directly from the same ancestor for a different physical run.
    other_run = uuid4()
    fork: RunnableConfig = {
        "configurable": {
            **run_configurable(str(chat.conversation.id), other_run, chat.context.tenant_id),
            "checkpoint_id": ancestor,
        }
    }
    await graph.ainvoke({"answers": ["fork"]}, fork)
    newest = await chat.saver.aget_tuple(config)
    assert newest is not None and newest.checkpoint["id"] != leaf.checkpoint["id"]
    assert newest.metadata.get("moldy_run_id") == str(other_run)
    await chat.set_selection(leaf.checkpoint["id"])
    assert await chat.read(raise_on_unavailable=True) is None
    await chat.set_selection(newest.checkpoint["id"])
    assert await chat.read() is None


async def test_temporarily_unavailable_saver_remains_retryable_for_dispatch(
    chat: PausedChat, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unavailable(config: RunnableConfig) -> None:
        raise RuntimeError("graph store temporarily unavailable")

    monkeypatch.setattr(chat.saver, "aget_tuple", unavailable)
    assert await chat.read() is None
    with pytest.raises(CheckpointUnavailable):
        await chat.read(raise_on_unavailable=True)


@pytest.mark.parametrize("foreign", ["org_id", "tenant_id", "user_id"])
async def test_context_denial_precedes_opening_the_graph_store(
    chat: PausedChat, monkeypatch: pytest.MonkeyPatch, foreign: str
) -> None:
    def forbidden_lookup() -> InMemorySaver:
        pytest.fail("a different actor must never open the graph store")

    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", forbidden_lookup)
    foreign_context = replace(chat.context, **{foreign: uuid4()})
    try:
        result = await read_pending_checkpoint(
            chat.db,
            conversation=chat.conversation,
            context=foreign_context,
            parent_run=chat.run,
        )
    except NotFoundError as error:
        assert error.status == 404
    else:
        assert result is None


async def test_legacy_checkpoint_requires_actual_matching_parent_evidence(chat: PausedChat) -> None:
    graph = chat.graph(form=ask(question("legacy")))
    legacy: RunnableConfig = {"configurable": {"thread_id": str(chat.conversation.id)}}
    initial = await graph.ainvoke({"answers": []}, legacy)
    iid = initial["__interrupt__"][0].id
    saved = await chat.saver.aget_tuple(legacy)
    assert saved is not None and "moldy_run_id" not in saved.metadata
    assert await chat.read() is None
    await chat.set_reported_interrupt(iid)
    pending = await chat.read()
    assert pending is not None and [item["id"] for item in pending.interrupts] == [iid]

    # An old journal can supply a missing namespace, but cannot invent another live slot.
    ids = (iid, "retired")
    chat.db.add(
        MessageEvent(
            conversation_id=chat.conversation.id,
            assistant_msg_id=str(chat.run.id),
            events=[
                stored_protocol_event(
                    run_id=str(chat.run.id),
                    thread_id=str(chat.conversation.id),
                    seq=index,
                    method="input.requested",
                    namespace=["child"],
                    data={"interrupt_id": identifier, "payload": {"unsafe": "journal-only"}},
                    event_id=f"interrupt-{index}",
                )
                for index, identifier in enumerate(ids, start=1)
            ],
            status="completed",
        )
    )
    await chat.set_reported_interrupt("retired")
    from_journal = await chat.read()
    assert from_journal is not None
    assert [item["id"] for item in from_journal.interrupts] == [iid]
    assert "unsafe" not in from_journal.interrupts[0]["value"]


async def test_checkpoint_with_wrong_recorded_company_cannot_use_legacy_fallback(
    chat: PausedChat,
) -> None:
    config: RunnableConfig = {
        "configurable": run_configurable(str(chat.conversation.id), chat.run.id, uuid4())
    }
    graph = chat.graph(form=ask(question("another company")))
    initial = await graph.ainvoke({"answers": []}, config)
    await chat.set_reported_interrupt(initial["__interrupt__"][0].id)
    assert await chat.read() is None


async def test_unknown_and_workflow_interrupts_do_not_supply_client_approval_forms(
    chat: PausedChat,
) -> None:
    graph = chat.graph(
        scheduled=ask({"type": "workflow_wait", "workflow_run_id": "internal-workflow"}),
        custom=ask({"type": "private_tool", "responseSchema": {"sensitive": "opaque"}}),
        mixed=ask({"type": "auth_required", **approval_payload()}),
    )
    result = await graph.ainvoke({"answers": []}, chat.config())
    await chat.set_reported_interrupt(result["__interrupt__"][0].id)
    saved = await chat.read()
    assert saved is not None
    for slot in result["__interrupt__"]:
        expected = "workflow_wait" if slot.value["type"] == "workflow_wait" else "unknown"
        assert saved.native_kinds[slot.id] == expected
        item = next(item for item in saved.interrupts if item["id"] == slot.id)
        assert item["value"] == {"type": expected}

    # The SDK can deserialize an old write without a unique ID into an Interrupt
    # with its default placeholder. Even a genuine approval payload cannot repair it.
    config = chat.config()
    snapshot = await graph.aget_state(config)
    task = next(task for task in snapshot.tasks if task.name == "mixed")
    missing_id = Interrupt(value=approval_payload())
    await chat.saver.aput_writes(snapshot.config, [("__interrupt__", [missing_id])], task.id)
    replaced = await graph.aget_state(config)
    stored = next(t for t in replaced.tasks if t.id == task.id)
    assert stored.interrupts[0].id == missing_id.id != task.interrupts[0].id
    assert await chat.read(raise_on_unavailable=True) is None


@pytest.mark.parametrize("wrong", ["decider", "digest", "org"])
async def test_durable_approval_checks_actual_owner_args_and_expiry(
    chat: PausedChat, wrong: str
) -> None:
    payload = approval_payload()
    graph = chat.graph(review=ask(payload))
    result = await graph.ainvoke({"answers": []}, chat.config())
    iid = result["__interrupt__"][0].id
    await chat.set_reported_interrupt(iid)
    (record,) = await record_pending(
        chat.db, chat.run, [PendingInterrupt(iid, payload)], origin=RunOrigin()
    )
    deadline = datetime(2035, 1, 1, tzinfo=UTC)
    record.expires_at = deadline
    await chat.db.commit()
    pending = await chat.read()
    assert pending is not None and [item["id"] for item in pending.interrupts] == [iid]
    assert pending.expires_at[iid].replace(tzinfo=UTC) == deadline
    assert pending.native_kinds[iid] == "approval"

    if wrong == "decider":
        record.decider_user_id = (await make_user(chat.db)).id
    elif wrong == "org":
        record.org_id = uuid4()
    else:
        record.args_digest = "0" * 64
    await chat.db.commit()
    blocked = await chat.read()
    assert blocked is None or not blocked.interrupts


@pytest.mark.parametrize("mismatch", ["decider", "digest"])
async def test_partial_parallel_approval_preserves_the_original_decider_digest_and_expiry(
    chat: PausedChat, mismatch: str
) -> None:
    payload = approval_payload()
    graph = chat.graph(left=ask(payload), right=ask(payload))
    config = chat.config()
    first = await graph.ainvoke({"answers": []}, config)
    left, right = (item.id for item in first["__interrupt__"])
    await chat.set_reported_interrupt(left)
    _finished, original = await record_pending(
        chat.db,
        chat.run,
        [PendingInterrupt(left, payload), PendingInterrupt(right, payload)],
        origin=RunOrigin(),
    )
    deadline = datetime(2035, 1, 1, tzinfo=UTC)
    original.expires_at = deadline
    await chat.db.commit()
    await graph.ainvoke(Command(resume={left: "done"}), config)
    child = await chat.continuation(right)
    inherited = await chat.read(child)
    assert inherited is not None and [item["id"] for item in inherited.interrupts] == [right]
    assert inherited.approval_run_ids[right] == (chat.run.id,)
    assert inherited.expires_at[right].replace(tzinfo=UTC) == deadline

    # A later recorder may create another row for the same still-unanswered slot.
    (later,) = await record_pending(
        chat.db, child, [PendingInterrupt(right, payload)], origin=RunOrigin()
    )
    later.expires_at = datetime(2036, 1, 1, tzinfo=UTC)
    await chat.db.commit()
    duplicate = await chat.read(child)
    assert duplicate is not None
    assert set(duplicate.approval_run_ids[right]) == {child.id, chat.run.id}
    assert duplicate.expires_at[right].replace(tzinfo=UTC) == deadline

    if mismatch == "decider":
        original.decider_user_id = (await make_user(chat.db)).id
    else:
        original.args_digest = "f" * 64
    await chat.db.commit()
    assert await chat.read(child) is None


async def test_per_action_choices_intersect_graph_and_every_durable_approval(
    chat: PausedChat,
) -> None:
    payload = approval_payload()
    for review in payload["review_configs"]:
        review["allowed_decisions"] = ["approve", "edit", "reject"]
    graph = chat.graph(question=ask(question("continue?")), review=ask(payload))
    config = chat.config()
    first = await graph.ainvoke({"answers": []}, config)
    slots = first["__interrupt__"]
    question_id = next(slot.id for slot in slots if slot.value.get("type") == "ask_user")
    approval_id = next(slot.id for slot in slots if "action_requests" in slot.value)
    await chat.set_reported_interrupt(question_id)
    request = PendingInterrupt(approval_id, payload)
    (original,) = await record_pending(chat.db, chat.run, [request], origin=RunOrigin())
    a, b = original.actions
    original.actions = [
        {**a, "allowed_decisions": ["reject"]},
        {**b, "allowed_decisions": ["approve", "edit", "respond"]},
    ]
    await chat.db.commit()

    await graph.ainvoke(Command(resume={question_id: "continue"}), config)
    child = await chat.continuation(approval_id)
    (later,) = await record_pending(chat.db, child, [request], origin=RunOrigin())
    a, b = later.actions
    later.actions = [
        {**a, "allowed_decisions": ["approve", "reject"]},
        {**b, "allowed_decisions": ["edit", "reject", "respond"]},
    ]
    await chat.db.commit()
    before = await chat.saver.aget_tuple(config)
    assert before is not None

    saved = await chat.read(child)
    assert saved is not None
    assert set(saved.approval_run_ids[approval_id]) == {chat.run.id, child.id}
    (approved_slot,) = saved.interrupts
    assert approved_slot["id"] == approval_id
    assert approved_slot["value"]["review_configs"] == [
        {"action_name": "execute_in_skill", "allowed_decisions": ["reject"]},
        {"action_name": "send_email", "allowed_decisions": ["edit"]},
    ]
    after = await chat.saver.aget_tuple(config)
    assert after == before and after is not None
    raw = next(
        slot.value
        for _, channel, writes in after.pending_writes or ()
        if channel == "__interrupt__"
        for slot in writes
        if slot.id == approval_id
    )
    assert [review["allowed_decisions"] for review in raw["review_configs"]] == [
        ["approve", "edit", "reject"],
        ["approve", "edit", "reject"],
    ]


@pytest.mark.parametrize("evidence", ["exact", "other_run", "old_id", "other_args", "other_index"])
async def test_session_consent_offer_requires_the_current_journal_action_at_the_same_index(
    chat: PausedChat, evidence: str
) -> None:
    payload = {
        "action_requests": [
            {"name": "test_skill_draft", "args": {"command": "python test.py"}},
            {"name": "finalize_skill", "args": {}},
        ],
        "review_configs": [
            {"action_name": "test_skill_draft", "allowed_decisions": ["approve", "reject"]},
            {"action_name": "finalize_skill", "allowed_decisions": ["approve", "reject"]},
        ],
    }
    graph = chat.graph(builder=ask(payload))
    config = chat.config()
    result = await graph.ainvoke({"answers": []}, config)
    ident = result["__interrupt__"][0].id
    await chat.set_reported_interrupt(ident)
    before = await chat.saver.aget_tuple(config)
    assert before is not None

    display = deepcopy(payload)
    if evidence == "other_args":
        display["action_requests"][0]["args"] = {"command": "python different.py"}
    if evidence == "other_index":
        display["action_requests"].reverse()
        display["review_configs"].reverse()
    journal = stored_protocol_event(
        run_id=str(uuid4() if evidence == "other_run" else chat.run.id),
        thread_id=str(chat.conversation.id),
        seq=1,
        method="input.requested",
        namespace=[],
        data={"interrupt_id": "retired" if evidence == "old_id" else ident, "payload": display},
    )
    annotate_session_consent_eligibility(journal, ["test_skill_draft"])
    # Even a persisted flag on an ineligible action cannot grant durable consent.
    for entry in display["review_configs"]:
        if entry["action_name"] == "finalize_skill":
            entry["session_consent_eligible"] = True
    chat.db.add(
        MessageEvent(
            conversation_id=chat.conversation.id,
            assistant_msg_id=str(chat.run.id),
            events=[journal],
            status="completed",
        )
    )
    await chat.db.commit()

    proof = await chat.read()
    assert proof is not None
    (pending,) = proof.interrupts
    assert pending["id"] == ident
    builder, finalizer = pending["value"]["review_configs"]
    assert bool(builder.get("session_consent_eligible")) == (evidence == "exact")
    assert "session_consent_eligible" not in finalizer
    after = await chat.saver.aget_tuple(config)
    assert after == before and after is not None
    raw_reviews = [
        review
        for _, channel, writes in after.pending_writes or ()
        if channel == "__interrupt__"
        for slot in writes
        for review in slot.value["review_configs"]
    ]
    assert raw_reviews == payload["review_configs"]
