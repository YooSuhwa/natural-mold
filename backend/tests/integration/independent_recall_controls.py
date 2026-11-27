"""A controlled wire shared by normal and independent prepared chat runs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz.conversation_run_scope import (
    RunConversationTarget,
    RunExecutionTarget,
    lock_executable_run,
)
from app.authz.org_context import OrgContext
from app.config import settings
from app.database import tenant_session
from app.dependencies import CurrentUser
from app.services import chat_service, memory_embedder, memory_index, memory_recall
from app.services.agent_execution_acceptance import OwnedExecutionRequest, accept_owned_execution
from app.services.prepared_run_adapter import (
    PreparedRunRequest,
    PreparedRunResolver,
    chat_config_resolver,
)


@dataclass
class RecallProbes:
    requests: list[str]
    model_lookup: AsyncMock
    backfill: AsyncMock

    def assert_unused(self) -> None:
        assert self.requests == []
        self.model_lookup.assert_not_called()
        self.backfill.assert_not_called()


def install_recall_probes(
    monkeypatch: pytest.MonkeyPatch, sessions: Callable[[], AsyncSession]
) -> RecallProbes:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append(path)
        if path == "/_analyze":
            return httpx.Response(200, json={"tokens": [{"token": "remote-index-token"}]})
        if path == "/embeddings":
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})
        raise AssertionError(f"Unexpected HTTP request to {request.url.host}{path}")

    wire = httpx.MockTransport(respond)

    async def intercepted(
        _transport: httpx.AsyncHTTPTransport, request: httpx.Request
    ) -> httpx.Response:
        return await wire.handle_async_request(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", intercepted)
    monkeypatch.setattr(settings, "opensearch_url", "https://nori.invalid")
    model = memory_embedder.OpenAIShapedEmbedder("test-embed", 2, None, "https://vectors.invalid")
    # Use the injected-provider interface with the real HTTP embedding implementation.
    provider = SimpleNamespace(model=model.model, dimensions=model.dimensions, embed=model.embed)
    lookup = AsyncMock(return_value=provider)
    backfill = AsyncMock(wraps=memory_index.enqueue_backfill)
    monkeypatch.setitem(memory_recall.VECTOR_FLOORS, f"{model.model}@{model.dimensions}", 0.5)
    monkeypatch.setattr(memory_embedder, "async_session", sessions)
    monkeypatch.setattr(memory_embedder, "resolve_embedder", lookup)
    monkeypatch.setattr(memory_index, "enqueue_backfill", backfill)
    return RecallProbes(requests, lookup, backfill)


async def verify_ordinary_preparation(
    sessions: Callable[[], AsyncSession],
    context: OrgContext,
    target_agent_id: UUID,
    tmp_path: Path,
    probes: RecallProbes,
) -> None:
    """An ordinary prepared conversation using the same target must contact both services."""
    async with tenant_session(context, maker=sessions) as db:
        chat = await chat_service.create_conversation(db, target_agent_id, context=context)
        accepted = await accept_owned_execution(
            db,
            OwnedExecutionRequest(
                conversation_id=chat.id,
                root_agent_id=target_agent_id,
                context=context,
                source="chat",
                input_preview="세션 만료 시간 알려줘",
            ),
        )
        await db.commit()

    target = RunExecutionTarget(
        accepted.run_id, RunConversationTarget(chat.id, target_agent_id, context)
    )
    user = CurrentUser(id=context.user_id, email="owner@test.invalid", name="Owner")
    resolver = PreparedRunResolver(
        PreparedRunRequest(context, chat_config_resolver(user, context), tmp_path)
    )
    async with tenant_session(context, maker=sessions) as owner:
        run = await lock_executable_run(owner, target)
        assert run is not None
        await resolver(owner, run)
        cfg = resolver.execution_cfg
        assert cfg is not None and not cfg.independent_job
        assert "세션 만료 시간은 30분이다" in (cfg.memory_recall_block or "")
        assert set(probes.requests) == {"/_analyze", "/embeddings"}
        probes.model_lookup.assert_awaited()
        await owner.rollback()
