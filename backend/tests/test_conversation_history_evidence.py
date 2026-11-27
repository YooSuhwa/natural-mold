from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypedDict

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    get_buffer_string,
)
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from sqlalchemy import delete, update

from app.agent_runtime import checkpointer
from app.agent_runtime.offload_storage import OffloadIdentity, ScopedOffloadBackend
from app.agent_runtime.offload_storage_types import LegacyHistoryKind, rollback_session_id
from app.authz.org_context import OrgContext
from app.dependencies import get_org_context
from app.models.agent import Agent
from app.models.conversation_run import ConversationRun
from app.models.message_event import MessageEvent
from app.models.organization import OrganizationMember
from app.models.user import User
from app.services import audit_service
from app.services import conversation_history_evidence as evidence
from tests.authz.agent_execution_runtime_fixture import seed_runtime_run
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TestSession


class _Saver:
    def __init__(self, thread: str, values: Mapping[str, Any]) -> None:
        self.thread = thread
        self.values = values
        self.calls = 0

    async def aget_tuple(self, config: dict[str, Any]) -> SimpleNamespace | None:
        self.calls += 1
        cid = config["configurable"].get("checkpoint_id")
        if cid not in ("cp0", "cp1") or config["configurable"]["thread_id"] != self.thread:
            return None
        parent = {
            "configurable": {"thread_id": self.thread, "checkpoint_ns": "", "checkpoint_id": "cp0"}
        }
        return SimpleNamespace(
            config=config,
            checkpoint={"channel_values": self.values if cid == "cp1" else {}},
            metadata={"source": "loop"},
            parent_config=parent if cid == "cp1" else None,
        )


async def _published(
    run_id: uuid.UUID, conversation_id: uuid.UUID, question: str, answer: str | None
) -> None:
    async with TestSession() as db:
        await db.execute(
            update(ConversationRun)
            .where(ConversationRun.id == run_id)
            .values(input_preview=question)
        )
        if answer is not None:
            payloads = [
                {"event": "message-start", "role": "ai", "id": "source-message"},
                {
                    "event": "content-block-finish",
                    "index": 0,
                    "content": {"type": "text", "text": answer},
                },
                {"event": "message-finish"},
            ]
            db.add(
                MessageEvent(
                    conversation_id=conversation_id,
                    assistant_msg_id=str(run_id),
                    events=[
                        {
                            "method": "messages",
                            "namespace": [],
                            "thread_id": str(conversation_id),
                            "run_id": str(run_id),
                            "seq": idx,
                            "data": {
                                **payload,
                                "metadata": {"run_id": "provider-call", "langgraph_node": "model"},
                            },
                        }
                        for idx, payload in enumerate(payloads, 1)
                    ],
                )
            )
        await db.commit()


async def test_archive_evidence_reads_verified_private_text_only(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, agent, conv = await seed_runtime_run()
    backend = ScopedOffloadBackend(
        tmp_path,
        OffloadIdentity(str(agent.user_id), str(conv.id), "run"),
        agent.id,
        archive_owner_id=str(conv.user_id),
    )
    messages = [
        HumanMessage(content="환율을 조사해 주세요."),
        ToolMessage(content="private-tool-args", tool_call_id="internal"),
        SystemMessage(content="private-system-instructions"),
        HumanMessage(content="injected", name="moldy_recalled_memories"),
        AIMessage(
            id="source-message",
            content=[
                {"type": "thinking", "thinking": "internal-reasoning"},
                {"type": "text", "text": "100원과 123-pretend-secret입니다."},
            ],
        ),
    ]
    await _published(run.id, conv.id, "환율을 조사해 주세요.", "100원과 <redacted>입니다.")
    ref = backend.archive_compaction("cp0", messages)
    values = {"_summarization_event": {"file_path": ref, "cutoff_index": len(messages)}}
    saver = _Saver(str(conv.id), values)
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: saver)
    monkeypatch.setattr(evidence, "runtime_data_dir", lambda: tmp_path)

    async def secrets(*args: Any) -> set[str]:
        return {"NEW-ROTATED-VALUE"}

    monkeypatch.setattr(evidence, "collect_strict_history_secrets", secrets)
    path = f"/api/conversations/{conv.id}/history-evidence/cp1"
    first = await client.get(path, params={"limit": 1})
    assert first.status_code == 200, first.text
    result = first.json()
    assert first.headers["cache-control"] == "private, no-store"
    assert result["format"] == "jsonl"
    assert result["cutoff_index"] == len(messages)
    projection = backend.project_reference(ref)
    assert projection is not None and result["history_id"] == projection.logical_id
    raw = b"".join((m.model_dump_json() + "\n").encode() for m in messages)
    assert result["content_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["byte_length"] == len(raw)
    assert result["excerpts"] == [
        {"role": "user", "text": "환율을 조사해 주세요.", "truncated": False}
    ]
    more = await client.get(
        path, params={"offset": result["next_offset"], "expected_sha256": result["content_sha256"]}
    )
    assert more.status_code == 200
    final = more.json()
    assert final["next_offset"] is None
    assert len(final["excerpts"]) == 1 and "100원" in final["excerpts"][0]["text"]
    for marker in [
        "private-tool-args",
        "private-system",
        "injected",
        "internal-reasoning",
        "123-pretend-secret",
        ".moldy-offload",
    ]:
        assert marker not in first.text + more.text
    assert (await client.get(path, params={"expected_sha256": "f" * 64})).status_code == 409
    assert (await client.get(path.replace("cp1", "missing"))).status_code == 404


@pytest.mark.parametrize("variant", ["scoped", "unscoped-session", "unscoped-conversation"])
async def test_old_single_file_checks_exact_checkpoint_and_digest(
    variant: str, client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, agent, conv = await seed_runtime_run()
    await _published(run.id, conv.id, "지난달 가격", None)
    session = f"session_{uuid.uuid4().hex}"
    leaf = f"{conv.id}.md" if variant == "unscoped-conversation" else f"{session}.md"
    request = HumanMessage(content="지난달 가격")
    later = AIMessage(content="다른 분기에 추가된 내용")
    raw = f"## Summarized at 2026\n\n{get_buffer_string([request, later], format='xml')}\n\n"
    if variant == "scoped":
        backend = ScopedOffloadBackend(
            tmp_path, OffloadIdentity(str(agent.user_id), str(conv.id), "run"), agent.id
        )
        path = f"{backend.artifacts_root}/conversation_history/{leaf}"
        assert not backend.write(path, raw).error
    else:
        path = f"/conversation_history/{leaf}"
        (tmp_path / "conversation_history").mkdir()
        (tmp_path / "conversation_history" / leaf).write_text(raw)
    values = {
        "_summarization_event": {"file_path": path, "cutoff_index": 1},
        "_summarization_session_id": session,
        "messages": [request, later],
    }
    saver = _Saver(str(conv.id), values)
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: saver)
    monkeypatch.setattr(evidence, "runtime_data_dir", lambda: tmp_path)
    url = f"/api/conversations/{conv.id}/history-evidence/cp1"
    if variant == "unscoped-session":
        # A checkpoint-provided session id does not identify a flat file's owner.
        assert (await client.get(url)).status_code == 404
        storage = ScopedOffloadBackend(
            tmp_path,
            OffloadIdentity(str(agent.user_id), str(conv.id), "run", frozenset({leaf})),
            agent.id,
        )
        storage.migrate_legacy_history(LegacyHistoryKind.SESSION, source_filename=leaf)
    response = await client.get(url)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["format"] == "legacy_markdown"
    assert data["content_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert [row["text"] for row in data["excerpts"]] == ["지난달 가격"]
    assert isinstance(later.content, str) and later.content not in response.text
    for forged in (
        f"/conversation_history/{uuid.uuid4()}.md",
        f"/conversation_history/session_{uuid.uuid4().hex}.md",
        path.replace("conversation_history/", "conversation_history/../"),
    ):
        values["_summarization_event"]["file_path"] = forged
        assert (await client.get(url)).status_code == 404


async def test_published_rollback_leaf_uses_conversation_owner_only(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, agent, conv = await seed_runtime_run()
    question = "제품별 금액을 알려주세요."
    await _published(run.id, conv.id, question, None)
    row = HumanMessage(content=question)
    backend = ScopedOffloadBackend(
        tmp_path,
        OffloadIdentity(str(agent.user_id), str(conv.id), "run"),
        agent.id,
        archive_owner_id=str(conv.user_id),
    )
    first = backend.archive_compaction("cp0", [row])
    session = rollback_session_id(first, "cp1")
    rolled = backend.archive_checkpoint_to_legacy(first, session, [AIMessage(content="새 응답")])
    saver = _Saver(
        str(conv.id),
        {"_summarization_event": {"file_path": rolled, "cutoff_index": 1}, "messages": [row]},
    )
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: saver)
    monkeypatch.setattr(evidence, "runtime_data_dir", lambda: tmp_path)
    response = await client.get(f"/api/conversations/{conv.id}/history-evidence/cp1")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["format"] == "legacy_markdown"
    assert result["excerpts"] == [{"role": "user", "text": question, "truncated": False}]


async def test_verified_import_whose_name_matches_rollback_uses_agent_owner_namespace(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, agent, conv = await seed_runtime_run()
    legacy_owner = uuid.uuid4()
    async with TestSession() as db:
        db.add(
            User(id=legacy_owner, email="shared-agent@example.invalid", name="Shared agent owner")
        )
        await db.flush()
        await db.execute(update(Agent).where(Agent.id == agent.id).values(user_id=legacy_owner))
        await db.commit()
    question = "수입 단가"
    await _published(run.id, conv.id, question, None)
    row = HumanMessage(content=question)
    session = "session_" + ("e" * 12) + "f13cf13c" + ("1" * 12)
    leaf = f"{session}.md"
    raw = get_buffer_string([row], format="xml")
    (tmp_path / "conversation_history").mkdir()
    (tmp_path / "conversation_history" / leaf).write_text(raw)
    storage = ScopedOffloadBackend(
        tmp_path,
        OffloadIdentity(str(legacy_owner), str(conv.id), "import", frozenset({leaf})),
        agent.id,
    )
    imported = storage.migrate_legacy_history(LegacyHistoryKind.SESSION, source_filename=leaf)
    saver = _Saver(
        str(conv.id),
        {
            "_summarization_event": {"file_path": imported.virtual_path, "cutoff_index": 1},
            "messages": [row],
        },
    )
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: saver)
    monkeypatch.setattr(evidence, "runtime_data_dir", lambda: tmp_path)
    response = await client.get(f"/api/conversations/{conv.id}/history-evidence/cp1")
    assert response.status_code == 200, response.text
    assert response.json()["content_sha256"] == imported.content_sha256
    assert response.json()["excerpts"][0]["text"] == question


@pytest.mark.parametrize("wrong", ["tenant", "org", "owner", "revoked"])
async def test_unauthorized_archive_reader_never_touches_checkpoint_pool(
    wrong: str, test_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, conv = await seed_runtime_run()
    monkeypatch.setattr(audit_service, "async_session", TestSession)
    urls = [f"/api/conversations/{cid}/history-evidence/cp1" for cid in (conv.id, uuid.uuid4())]
    own = unit_context(conv.user_id)
    context = OrgContext(
        tenant_id=uuid.uuid4() if wrong == "tenant" else own.tenant_id,
        org_id=uuid.uuid4() if wrong == "org" else own.org_id,
        user_id=uuid.uuid4() if wrong == "owner" else own.user_id,
    )
    if wrong == "revoked":
        async with TestSession() as db:
            await db.execute(
                delete(OrganizationMember).where(OrganizationMember.user_id == conv.user_id)
            )
            await db.commit()
    monkeypatch.setitem(test_app.dependency_overrides, get_org_context, lambda: context)
    monkeypatch.setattr(
        checkpointer, "get_checkpointer", lambda: pytest.fail("unauthorized pool access")
    )
    known, unknown = [await client.get(url) for url in urls]
    assert known.status_code == unknown.status_code == (403 if wrong in {"org", "revoked"} else 404)
    assert known.json() == unknown.json()


class _LoopState(TypedDict, total=False):
    question: str
    _summarization_event: dict[str, str]
    step: str


async def test_actual_graph_checkpoint_origin_and_cross_branch_update(
    client: AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, agent, conv = await seed_runtime_run()
    await _published(run.id, conv.id, "가격 비교", None)
    saver = InMemorySaver()
    backend = ScopedOffloadBackend(
        tmp_path,
        OffloadIdentity(str(agent.user_id), str(conv.id), "run"),
        agent.id,
        archive_owner_id=str(conv.user_id),
    )

    def compact(state: _LoopState, runtime: Runtime) -> _LoopState:
        info = runtime.execution_info
        assert info and info.checkpoint_id
        ref = backend.archive_compaction(
            info.checkpoint_id, [HumanMessage(content=state.get("question", ""))]
        )
        return {"_summarization_event": {"file_path": ref}}

    def finalize(state: _LoopState) -> _LoopState:
        return {"step": "done"}

    builder = StateGraph(_LoopState)
    builder.add_node("compact", compact)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "compact")
    builder.add_edge("compact", "finalize")
    builder.add_edge("finalize", END)
    graph = builder.compile(checkpointer=saver)
    config: RunnableConfig = {"configurable": {"thread_id": str(conv.id)}}
    await graph.ainvoke({"question": "가격 비교"}, config)
    monkeypatch.setattr(checkpointer, "get_checkpointer", lambda: saver)
    monkeypatch.setattr(evidence, "runtime_data_dir", lambda: tmp_path)
    tip = await graph.aget_state(config)
    url = f"/api/conversations/{conv.id}/history-evidence/"
    response = await client.get(url + tip.config.get("configurable", {})["checkpoint_id"])
    assert response.status_code == 200, response.text
    assert response.json()["excerpts"][0]["text"] == "가격 비교"
    # CP for the producer is the parent's parent of this tip. Copy its archive
    # to a different branch via the same state-update operation as the HTTP SDK.
    assert tip.parent_config
    compacted = await graph.aget_state(tip.parent_config)
    assert compacted.parent_config
    forged = await graph.aupdate_state(
        compacted.parent_config,
        {"_summarization_event": tip.values["_summarization_event"]},
        as_node="compact",
    )
    assert (
        await client.get(url + forged.get("configurable", {})["checkpoint_id"])
    ).status_code == 404
