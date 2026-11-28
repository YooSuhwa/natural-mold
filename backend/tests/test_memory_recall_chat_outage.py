"""Exercise default chat through an actual HTTP failure at its approved embedding service."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient, Request, Response
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.e2e_scripted_model import E2EScriptedChatModel
from app.config import settings
from app.models.memory import MemoryRecall, MemoryRecallRun
from app.services import memory_lifecycle, memory_vectors, model_service
from tests.authz.unit_scope_fixture import unit_context
from tests.conftest import TEST_USER_ID, TestSession
from tests.integration._seed import seed_conversation_with_agent
from tests.integration.run_status_wait import wait_for_run_status
from tests.test_memory_recall import _save
from tests.test_memory_recall_policy import _evaluated_report
from tests.test_memory_vectors import _embedding, _tenant


@pytest.mark.asyncio
async def test_regular_chat_completes_with_only_keyword_memory_after_embedding_outage(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    question = "주간 보고서 양식"
    approved = "주간 보고서 양식은 파란 표지로 작성한다."
    revoked = "주간 보고서 양식은 보라색 봉투에 넣는다."
    answer = "파란 표지를 사용합니다."
    conversation_id = await seed_conversation_with_agent(model_provider="openai_compatible")
    context = unit_context(TEST_USER_ID)
    seen: list[list[BaseMessage]] = []
    sent_to_embedding: list[tuple[bytes, dict[str, Any]]] = []

    async def unavailable(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request, *headers = (await reader.readuntil(b"\r\n\r\n")).split(b"\r\n")
            length = next(
                h.split(b":", 1)[1] for h in headers if h.lower().startswith(b"content-length:")
            )
            sent_to_embedding.append((request, json.loads(await reader.readexactly(int(length)))))
            writer.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    class CaptureChat(E2EScriptedChatModel):
        def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
            seen.append(list(messages))
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=answer))])

    send = AsyncClient.send

    async def local_only(client: AsyncClient, request: Request, **kwargs: Any) -> Response:
        assert request.url.host in {"test", "127.0.0.1"}, "the offline fixture only uses loopback"
        return await send(client, request, **kwargs)

    monkeypatch.setattr(AsyncClient, "send", local_only)
    monkeypatch.setattr(settings, "guardrail_pii_model_url", "")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setattr(settings, "memory_recall_quality_dir", tmp_path)
    runtime_data = tmp_path / "runtime-data"
    monkeypatch.setattr("app.agent_runtime.runtime_component_builder._DATA_DIR", runtime_data)
    monkeypatch.setattr("app.agent_runtime.runtime_config._DATA_DIR", runtime_data)
    monkeypatch.setattr("app.database.async_session", TestSession)
    monkeypatch.setattr("app.agent_runtime.runtime.usage_guard.async_session", TestSession)
    saver = InMemorySaver()
    monkeypatch.setattr("app.agent_runtime.checkpointer.get_checkpointer", lambda: saver)
    monkeypatch.setattr("app.services.conversation_run_worker.get_checkpointer", lambda: saver)
    monkeypatch.setattr(
        "app.agent_runtime.runtime_component_builder._build_model_candidates",
        lambda _cfg: [CaptureChat(slow_stream_delay_seconds=0)],
    )

    async with await asyncio.start_server(unavailable, "127.0.0.1", 0) as server:
        url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1"
        model = await model_service.create_model(db, data=_embedding(base_url=url))
        tenant = await _tenant(db)
        await db.commit()
        # Isolated policy fixture opens the serving path even though no deployed model is qualified.
        report = _evaluated_report(model.id)
        report["candidate"]["base_url"] = url
        path = tmp_path / str(tenant.id) / f"{model.id}.json"
        path.parent.mkdir()
        path.write_text(json.dumps(report))
        await memory_vectors.change(db, tenant, context, model_id=model.id, confirm_count=0)
        enrolled = await memory_vectors.change(
            db, tenant, context, model_id=model.id, confirm_count=0, recall_mode="serve"
        )
        assert enrolled.recall_mode == "serve"
        keep = await _save(db, approved)
        remove = await _save(db, revoked)
        assert await memory_lifecycle.revoke(db, context=context, memory_id=remove.id, reason=None)
        response = await client.post(
            f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/commands",
            json={
                "id": 1,
                "method": "run.start",
                "params": {"input": {"messages": [{"role": "user", "content": question}]}},
            },
        )
        assert response.status_code == 200, response.text
        run_id = response.json()["result"]["run_id"]
        await wait_for_run_status(run_id, "completed")

    assert sent_to_embedding == [
        (
            b"POST /v1/embeddings HTTP/1.1",
            {"input": [question], "model": model.model_name, "dimensions": 1024},
        )
    ]
    assert len(seen) == 1
    assert seen[0][-1].content == question
    assert any(approved in str(message.content) for message in seen[0][:-1])
    assert all(revoked not in str(message.content) for message in seen[0])
    recall = await db.scalar(select(MemoryRecallRun).where(MemoryRecallRun.run_id == run_id))
    assert recall is not None and (recall.mode, recall.fallback) == (
        "fresh",
        "embedding_unavailable",
    )
    assert list(
        await db.scalars(select(MemoryRecall.record_id).where(MemoryRecall.run_id == run_id))
    ) == [keep.id]
    replay = await client.post(
        f"/api/conversations/{conversation_id}/langgraph/threads/{conversation_id}/stream/events",
        json={"channels": ["messages"]},
    )
    assert replay.status_code == 200 and answer in replay.text
    assert revoked not in replay.text
