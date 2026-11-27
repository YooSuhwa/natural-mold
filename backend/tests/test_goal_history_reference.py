"""Link verified goal files to the producing run's own immutable chat archive."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import checkpointer, runtime_config
from app.agent_runtime.langgraph_event_projection import compaction_event
from app.agent_runtime.offload_storage import OffloadIdentity, ScopedOffloadBackend
from app.authz.org_context import OrgContext
from app.config import settings
from app.dependencies import get_org_context
from app.models.conversation import Conversation
from app.models.conversation_artifact import ArtifactVersion
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent, MessageEventChunk
from app.services import audit_service
from app.tenancy.checkpoint_audit import checkpoint_tenant
from tests.artifact_test_helpers import seed_artifact_conversation
from tests.authz.unit_scope_fixture import TEST_ORG_ID
from tests.conftest import TestSession
from tests.test_conversation_goals import _finish, _goal, _recorder, _settle, _start
from tests.test_conversation_history_evidence import _published

_QUESTION = "목표 비교 파일을 만들 때 사용한 자료"


@dataclass(frozen=True)
class GoalFile:
    conversation: Conversation
    run_id: UUID
    version_id: UUID
    goal_id: UUID
    version: int

    @property
    def path(self) -> str:
        base = f"/api/conversations/{self.conversation.id}/goal/evidence"
        return f"{base}/{self.version_id}/history-reference"

    @property
    def params(self) -> dict[str, str | int]:
        return {"goal_id": str(self.goal_id), "goal_version": self.version}


@pytest.fixture
async def goal_file(
    db: AsyncSession, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> GoalFile:
    cid, _ = await seed_artifact_conversation()
    conv = await db.get(Conversation, cid)
    assert conv is not None
    monkeypatch.setattr(settings, "conversation_goal_org_ids", frozenset({TEST_ORG_ID}))
    monkeypatch.setattr(settings, "artifact_storage_dir", str(tmp_path / "files"))
    monkeypatch.setattr(runtime_config, "_DATA_DIR", tmp_path)
    goal = await _goal(client, conv)
    run = await _start(db, conv)
    output = tmp_path / "output"
    recorder = await _recorder(conv, run, output)
    (output / "comparison.csv").write_text("업체,비용\n가,10\n")
    assert await recorder.collect_after_tool_result(tool_name="write_file", tool_call_id="compare")
    await _published(run.id, cid, _QUESTION, None)
    await _finish(db, run)
    await _settle(db, conv, run)
    current = (await client.get(f"/api/conversations/{cid}/goal")).json()["goal"]
    check = current["last_evidence"]["checks"][0]
    assert current["status"] == "completed"
    assert (
        check["passed"] and check["reason"] == "verified" and check["source_run_id"] == str(run.id)
    )
    return GoalFile(conv, run.id, UUID(check["version_id"]), UUID(goal["id"]), goal["version"])


class State(TypedDict, total=False):
    question: str
    compact: bool
    _summarization_event: dict[str, object]
    ended: bool


def _graph(
    file: GoalFile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[CompiledStateGraph[State, None, State, State], ScopedOffloadBackend]:
    conv = file.conversation
    assert conv.agent_id
    saver = InMemorySaver()
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: saver)
    storage = ScopedOffloadBackend(
        tmp_path, OffloadIdentity(str(conv.user_id), str(conv.id), str(file.run_id)), conv.agent_id
    )

    def summarize(state: State, runtime: Runtime) -> State:
        if not state.get("compact"):
            return {}
        info = runtime.execution_info
        assert info and info.checkpoint_id
        ref = storage.archive_compaction(
            info.checkpoint_id, [HumanMessage(content=state.get("question", ""))]
        )
        return {"_summarization_event": {"file_path": ref, "cutoff_index": 1}}

    def finalize(state: State) -> State:
        return {"ended": True}

    graph = StateGraph(State)
    graph.add_node("summarize", summarize)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "summarize")
    graph.add_edge("summarize", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=saver), storage


def _config(file: GoalFile, run_id: UUID, parent: str | None = None) -> RunnableConfig:
    configurable = {
        "thread_id": str(file.conversation.id),
        "checkpoint_ns": "",
        "moldy_run_id": str(run_id),
        **checkpoint_tenant(file.conversation.tenant_id),
    }
    if parent:
        configurable["checkpoint_id"] = parent
    return {"configurable": configurable}


async def _publish_done(
    db: AsyncSession,
    file: GoalFile,
    history_id: str,
    *,
    cutoff: int = 1,
    in_chunk: bool = False,
    duplicate_history_ids: tuple[str, ...] = (),
) -> None:
    events = [
        compaction_event(
            run_id=str(file.run_id),
            thread_id=str(file.conversation.id),
            seq=1,
            state="done",
            history_id=reference_id,
            cutoff_index=cutoff,
        )
        for reference_id in (history_id, *duplicate_history_ids)
    ]
    header = MessageEvent(
        assistant_msg_id=str(file.run_id),
        conversation_id=file.conversation.id,
        events=[] if in_chunk else events,
    )
    db.add(header)
    await db.flush()
    if in_chunk:
        db.add(
            MessageEventChunk(
                message_event_id=header.id,
                conversation_id=file.conversation.id,
                assistant_msg_id=str(file.run_id),
                seq_start=1,
                seq_end=1,
                events=events,
            )
        )
    await db.commit()


@pytest.mark.parametrize("in_chunk", [False, True], ids=["header", "chunk"])
async def test_goal_links_to_verified_source_branch_and_pins_evidence(
    goal_file: GoalFile,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    db: AsyncSession,
    client: AsyncClient,
    in_chunk: bool,
) -> None:
    graph, storage = _graph(goal_file, tmp_path, monkeypatch)
    neutral = _config(goal_file, uuid4())
    await graph.ainvoke({"question": "공통 시작", "compact": False}, neutral)
    fork = (await graph.aget_state(neutral)).config.get("configurable", {})["checkpoint_id"]
    source = _config(goal_file, goal_file.run_id)
    await graph.ainvoke(
        {"question": _QUESTION, "compact": True}, _config(goal_file, goal_file.run_id, fork)
    )
    source_tip = await graph.aget_state(source)
    ref = storage.project_reference(source_tip.values["_summarization_event"]["file_path"])
    assert ref
    await _publish_done(db, goal_file, ref.logical_id, in_chunk=in_chunk)

    # The active sibling contains a genuine archive too, but its run did not create the goal file.
    sibling_id = uuid4()
    await graph.ainvoke(
        {"question": "다른 분기의 자료", "compact": True}, _config(goal_file, sibling_id, fork)
    )
    sibling = await graph.aget_state(_config(goal_file, sibling_id))
    goal_file.conversation.active_branch_checkpoint_id = sibling.config.get("configurable", {})[
        "checkpoint_id"
    ]
    await db.commit()
    before = (await client.get(f"/api/conversations/{goal_file.conversation.id}/goal")).json()
    before_runs = await db.scalar(select(func.count()).select_from(ConversationRun))
    got = await client.get(goal_file.path, params=goal_file.params)
    assert got.status_code == 200, got.text
    reference = got.json()
    assert reference["source_run_id"] == str(goal_file.run_id)
    assert reference["history_id"] == ref.logical_id
    assert reference["cutoff_index"] == 1
    stored = await graph.aget_state(
        _config(goal_file, goal_file.run_id, reference["checkpoint_id"])
    )
    assert stored.metadata and stored.metadata.get("moldy_run_id") == str(goal_file.run_id)
    assert reference["checkpoint_id"] != goal_file.conversation.active_branch_checkpoint_id

    base = f"/api/conversations/{goal_file.conversation.id}/history-evidence"
    history = f"{base}/{reference['checkpoint_id']}"
    page = await client.get(history)
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["format"] == "jsonl"
    assert body["history_id"] == ref.logical_id
    assert [entry["text"] for entry in body["excerpts"]] == [_QUESTION]
    assert (
        await client.get(history, params={"expected_sha256": body["content_sha256"]})
    ).status_code == 200
    assert (await client.get(history, params={"expected_sha256": "e" * 64})).status_code == 409
    assert (
        await client.get(f"/api/conversations/{goal_file.conversation.id}/goal")
    ).json() == before
    assert await db.scalar(select(func.count()).select_from(ConversationRun)) == before_runs

    # A fork replay can carry the same run metadata without being the delivered stream.
    # Never present its archive via the file receipt, even if it is the latest run-tagged tip.
    await graph.ainvoke(
        {"question": "전달되지 않은 재생", "compact": True},
        _config(goal_file, goal_file.run_id, fork),
    )
    replayed = await client.get(goal_file.path, params=goal_file.params)
    assert replayed.status_code == 200
    assert replayed.json() is None or replayed.json() == reference


@pytest.mark.parametrize(
    "variant",
    ["empty", "inherited", "client_update", "wrong_history", "wrong_cutoff", "unpublished"],
)
async def test_goal_does_not_borrow_an_archive_from_other_runs_or_state_edits(
    goal_file: GoalFile,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: AsyncClient,
    variant: str,
    db: AsyncSession,
) -> None:
    graph, storage = _graph(goal_file, tmp_path, monkeypatch)
    other = _config(goal_file, uuid4())
    await graph.ainvoke({"question": "다른 실행", "compact": variant != "empty"}, other)
    other_tip = await graph.aget_state(other)
    source = _config(goal_file, goal_file.run_id)
    has_source_event = variant in {"wrong_history", "wrong_cutoff", "unpublished"}
    await graph.ainvoke({"question": _QUESTION, "compact": has_source_event}, source)
    source_tip = await graph.aget_state(source)
    if variant not in {"empty", "unpublished"}:
        ref = storage.project_reference(source_tip.values["_summarization_event"]["file_path"])
        assert ref
        await _publish_done(
            db,
            goal_file,
            "history_" + "a" * 24 if variant == "wrong_history" else ref.logical_id,
            cutoff=7 if variant in {"client_update", "wrong_cutoff"} else 1,
        )
    if variant == "client_update":
        forged = dict(other_tip.values["_summarization_event"], cutoff_index=7)
        await graph.aupdate_state(source, {"_summarization_event": forged}, as_node="summarize")
    response = await client.get(goal_file.path, params=goal_file.params)
    assert response.status_code == 200 and response.json() is None, response.text


@pytest.mark.parametrize("in_chunk", [False, True], ids=["header", "chunk"])
async def test_conflicting_done_markers_in_one_batch_hide_the_goal_link(
    goal_file: GoalFile,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: AsyncClient,
    db: AsyncSession,
    in_chunk: bool,
) -> None:
    graph, storage = _graph(goal_file, tmp_path, monkeypatch)
    source = _config(goal_file, goal_file.run_id)
    await graph.ainvoke({"question": _QUESTION, "compact": True}, source)
    tip = await graph.aget_state(source)
    ref = storage.project_reference(tip.values["_summarization_event"]["file_path"])
    assert ref
    await _publish_done(
        db,
        goal_file,
        ref.logical_id,
        in_chunk=in_chunk,
        duplicate_history_ids=(ref.logical_id, "history_" + "0" * 24),
    )
    response = await client.get(goal_file.path, params=goal_file.params)
    assert response.status_code == 200 and response.json() is None, response.text


async def test_old_goal_version_and_mismatched_receipt_have_no_history_reference(
    goal_file: GoalFile,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    graph, storage = _graph(goal_file, tmp_path, monkeypatch)
    source = _config(goal_file, goal_file.run_id)
    await graph.ainvoke({"question": _QUESTION, "compact": True}, source)
    ref = storage.project_reference(
        (await graph.aget_state(source)).values["_summarization_event"]["file_path"]
    )
    assert ref
    await _publish_done(db, goal_file, ref.logical_id)
    for override in ({"goal_version": goal_file.version + 1}, {"goal_id": str(uuid4())}):
        result = await client.get(goal_file.path, params={**goal_file.params, **override})
        assert result.status_code == 404 or (result.status_code == 200 and result.json() is None)
    version = await db.get(ArtifactVersion, goal_file.version_id)
    assert version
    version.sha256 = "a" * 64
    await db.commit()
    unavailable = await client.get(goal_file.path, params=goal_file.params)
    assert unavailable.status_code == 404 or (
        unavailable.status_code == 200 and unavailable.json() is None
    )


@pytest.mark.parametrize("foreign", ["owner", "org"])
async def test_goal_reference_checks_private_scope_before_touching_saver(
    goal_file: GoalFile,
    monkeypatch: pytest.MonkeyPatch,
    client: AsyncClient,
    test_app: FastAPI,
    foreign: str,
) -> None:
    conv = goal_file.conversation
    context = OrgContext(
        tenant_id=conv.tenant_id,
        org_id=uuid4() if foreign == "org" else conv.org_id,
        user_id=uuid4() if foreign == "owner" else conv.user_id,
    )
    monkeypatch.setitem(test_app.dependency_overrides, get_org_context, lambda: context)
    monkeypatch.setattr(audit_service, "async_session", TestSession)
    monkeypatch.setattr(
        checkpointer, "get_checkpointer", lambda: pytest.fail("private scope bypassed")
    )
    unknown_path = goal_file.path.replace(str(conv.id), str(uuid4()))
    known, unknown = [
        await client.get(path, params=goal_file.params) for path in (goal_file.path, unknown_path)
    ]
    assert known.status_code == unknown.status_code == 404
    assert known.json() == unknown.json()
