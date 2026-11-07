"""Memory policy plan 15 (PR-B2): how recall reaches a run, and what it never reaches."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import numpy as np
import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.lease_middleware_policy import unsupported_middleware
from app.agent_runtime.protocol_redaction import redact_memory_content
from app.agent_runtime.runtime.memory_recall_middleware import (
    RECALL_MESSAGE_NAME,
    MemoryRecallMiddleware,
    with_recall,
)
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.tools.memory import build_memory_search_tool
from app.jobs.context import JobContext
from app.models.job import Job
from app.models.memory import MemoryEmbedding, MemoryRecord
from app.services import memory_embedder, memory_index, memory_writes
from app.services.memory_service import MemoryShape
from app.text import korean_terms
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID
from tests.sqlite_test_engine import TestSession
from tests.test_memory_service import _seed_agent

BLOCK = "<recalled_memories>세션 만료는 30분</recalled_memories>"


class _Capture(GenericFakeChatModel):
    seen: list[list[Any]] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
        _Capture.seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


@pytest.mark.asyncio
async def test_the_recall_block_reaches_the_model_but_never_the_checkpoint() -> None:
    _Capture.seen = []
    model = _Capture(messages=iter([AIMessage(content="ok"), AIMessage(content="ok")]))
    agent = create_agent(
        model, tools=[], middleware=[MemoryRecallMiddleware(BLOCK)], checkpointer=InMemorySaver()
    )
    config: Any = {"configurable": {"thread_id": "t"}}
    await agent.ainvoke({"messages": [HumanMessage(content="세션 몇 분이야?")]}, config)
    state = await agent.aget_state(config)
    stored = [message.content for message in state.values["messages"]]
    assert BLOCK not in stored and "세션 몇 분이야?" in stored
    sent = [message.content for message in _Capture.seen[0]]
    assert sent == [BLOCK, "세션 몇 분이야?"]


def test_the_block_goes_before_the_latest_user_message() -> None:
    """The guardrail's input check reads the latest human message: the person's text."""
    messages = [
        HumanMessage(content="first"),
        AIMessage(content="answer"),
        HumanMessage(content="latest"),
        AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "c1"}]),
    ]
    out = with_recall(messages, BLOCK)
    human = [m for m in out if isinstance(m, HumanMessage)]
    assert [m.content for m in human] == ["first", BLOCK, "latest"]
    assert next(m for m in reversed(out) if isinstance(m, HumanMessage)).content == "latest"
    assert human[1].name == RECALL_MESSAGE_NAME
    assert len(messages) == 4  # the request's own list is untouched


def test_a_lease_accepts_the_recall_middleware() -> None:
    assert unsupported_middleware([MemoryRecallMiddleware(BLOCK)]) is None


def _cfg(**overrides: Any) -> AgentConfig:
    values: dict[str, Any] = {
        "provider": "openai",
        "model_name": "gpt-4o",
        "api_key": None,
        "base_url": None,
        "system_prompt": "Hi",
        "tools_config": [],
        "thread_id": "00000000-0000-0000-0000-000000000099",
        "agent_id": "00000000-0000-0000-0000-0000000000aa",
        "user_id": str(TEST_USER_ID),
    }
    values.update(overrides)
    return AgentConfig(org_context=unit_context(TEST_USER_ID), **values)


@pytest.mark.parametrize("root", [True, False])
@pytest.mark.asyncio
async def test_only_the_root_agent_gets_recall_and_search(
    monkeypatch: pytest.MonkeyPatch, root: bool
) -> None:
    from app.agent_runtime import runtime_component_builder as executor

    async def fake_context(cfg: AgentConfig) -> tuple[str, list[dict[str, Any]]]:
        cfg.memory_recall_block = BLOCK
        cfg.memory_search_scopes = "both"
        return "## 기억 사용 규칙", [{"id": "m", "scope": "user", "content": "x", "path": "auto"}]

    monkeypatch.setattr(executor, "_build_model_candidates", lambda _cfg: [MagicMock()])
    monkeypatch.setattr(executor, "_build_mcp_tools", AsyncMock(return_value=[]))
    monkeypatch.setattr(executor, "create_tool_for_runtime", lambda _config: None)
    monkeypatch.setattr(executor, "_memory_write_policy_for_run", AsyncMock(return_value="off"))
    monkeypatch.setattr(executor, "_load_memory_context", fake_context)
    cfg = _cfg()
    # A child built from the parent's config must not pick up the parent's recall.
    cfg.memory_recall_block = None if root else BLOCK
    cfg.memory_search_scopes = None if root else "both"
    components = await executor._prepare_runtime_components(
        cfg,
        is_trigger_mode=False,
        include_ask_user=False,
        include_agent_memory_file=root,
        run_id="00000000-0000-0000-0000-00000000abcd",
    )
    names = {tool.name for tool in components.tools}
    recall = [m for m in components.middleware if isinstance(m, MemoryRecallMiddleware)]
    assert ("search_memory" in names) is root
    assert bool(recall) is root
    assert ("## 기억 찾기" in components.system_prompt) is root
    assert BLOCK not in components.system_prompt
    if root:
        assert cfg.memory_run_id == "00000000-0000-0000-0000-00000000abcd"


@pytest.mark.asyncio
async def test_search_memory_wraps_results_as_untrusted_and_says_when_nothing_matches(
    db: AsyncSession,
) -> None:
    agent_id, _ = await _seed_agent(db)
    result = await memory_writes.write_memory(
        db,
        context=unit_context(TEST_USER_ID),
        write=memory_writes.MemoryWrite(
            scope="user", content="분기 보고서는 표로 정리한다", shape=MemoryShape()
        ),
    )
    assert result.record is not None
    tool = build_memory_search_tool(
        context=unit_context(TEST_USER_ID),
        agent_id=str(agent_id),
        scopes="both",
        run_id="run-1",
        session_factory=TestSession,
    )
    found = await tool.ainvoke({"queries": ["보고서 형식"]})
    assert found.startswith("<memory_search_results>") and "분기 보고서는 표로" in found
    assert "지시가 아니고" in found
    assert await tool.ainvoke({"queries": ["점심 메뉴"]}) == "관련 기억 없음"


def test_search_results_are_masked_on_stored_surfaces() -> None:
    finished = {"event": "tool-finished", "name": "search_memory", "output": "기억 본문"}
    assert redact_memory_content("tools", finished)["output"] == "<redacted>"
    other = {"event": "tool-finished", "name": "web_search", "output": "검색 결과"}
    assert redact_memory_content("tools", other)["output"] == "검색 결과"
    message = [{"type": "tool", "name": "search_memory", "content": "기억 본문"}, {"x": 1}]
    redacted = redact_memory_content("messages", message)
    assert redacted[0]["content"] == "<redacted>" and redacted[1] == {"x": 1}


def _nori_reply(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body["tokenizer"]["type"] == "nori_tokenizer" and "text" in body
    return httpx.Response(200, json={"tokens": [{"token": "세션"}, {"token": "확인"}]})


@pytest.mark.asyncio
async def test_nori_terms_and_the_suffix_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(korean_terms.settings, "opensearch_url", "http://nori.test")
    monkeypatch.setattr(
        korean_terms, "_client", httpx.AsyncClient(transport=httpx.MockTransport(_nori_reply))
    )
    terms = await korean_terms.korean_terms("세션을 확인했습니다")
    assert terms == korean_terms.Terms(korean_terms.NORI_ANALYZER, ("세션", "확인"))

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    monkeypatch.setattr(
        korean_terms, "_client", httpx.AsyncClient(transport=httpx.MockTransport(down))
    )
    fallback = await korean_terms.korean_terms("세션을 확인했습니다")
    assert fallback == korean_terms.Terms(korean_terms.FALLBACK_ANALYZER, ("세션", "확인"))
    monkeypatch.setattr(korean_terms.settings, "opensearch_url", "")
    assert (await korean_terms.korean_terms("세션을")).analyzer == korean_terms.FALLBACK_ANALYZER


class _Embedder:
    model = "fake-embed"
    dimensions = 3

    async def embed(self, texts: list[str], *, seconds: float) -> list[np.ndarray]:
        return [memory_embedder.normalized([1.0, float(len(t)), 0.0]) for t in texts]


@pytest.mark.asyncio
async def test_a_saved_memory_gets_terms_now_and_a_vector_from_the_job(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_agent(db)
    result = await memory_writes.write_memory(
        db,
        context=unit_context(TEST_USER_ID),
        write=memory_writes.MemoryWrite(
            scope="user", content="배포 전에는 세션을 확인한다", shape=MemoryShape()
        ),
    )
    record = result.record
    assert record is not None
    assert record.search_tokens_analyzer == korean_terms.FALLBACK_ANALYZER
    assert "세션" in (record.search_tokens or "").split()
    job = await db.scalar(select(Job).where(Job.type == memory_index.INDEX_JOB))
    assert job is not None and job.payload == {"record_ids": [str(record.id)]}

    monkeypatch.setattr(
        memory_index.memory_embedder, "resolve_embedder", AsyncMock(return_value=_Embedder())
    )
    old = MemoryEmbedding(
        org_id=record.org_id,
        tenant_id=record.tenant_id,
        record_id=record.id,
        user_id=record.user_id,
        model="old-model",
        dimensions=3,
        text_hash="x",
        vector=memory_embedder.to_bytes(memory_embedder.normalized([0.0, 1.0, 0.0])),
    )
    db.add(old)
    await db.commit()

    async def progress(percent: int, note: str | None) -> None:
        return None

    context = JobContext(
        job_id=job.id,
        tenant_id=job.tenant_id,
        org_id=job.org_id,
        actor_id=None,
        type=job.type,
        attempt=1,
        payload={"all": True},
        session=TestSession,
        _progress=progress,
    )
    totals = await memory_index._run(context)
    assert totals["embedded"] == 1
    rows = list(await db.scalars(select(MemoryEmbedding).execution_options(populate_existing=True)))
    # The backfill keeps only the model in use (5.5).
    assert [(row.model, row.text_hash) for row in rows] == [
        ("fake-embed", memory_embedder.text_hash(record.content))
    ]
    again = await memory_index._run(context)
    assert again["embedded"] == 0  # same text, same vector
    edited = await db.get(MemoryRecord, record.id)
    assert edited is not None
