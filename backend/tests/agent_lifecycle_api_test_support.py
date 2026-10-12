from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass
from typing import Literal, assert_never, cast

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Content

type CompatSurface = Literal[
    "native_stream",
    "openai_stream",
    "dify_blocking",
    "thread_wait",
]


@dataclass(frozen=True, slots=True)
class ApiRuntimeCase:
    client: AsyncClient
    db: AsyncSession
    monkeypatch: pytest.MonkeyPatch


@dataclass(frozen=True, slots=True)
class CompatRequest:
    surface: CompatSurface
    deployment_public_id: str
    cleartext_key: str


@pytest.fixture
def api_runtime_case(
    client: AsyncClient,
    db: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> ApiRuntimeCase:
    return ApiRuntimeCase(client, db, monkeypatch)


async def consume_until(
    body_iterator: AsyncIterable[Content],
    marker: str,
) -> AsyncGenerator[str | bytes, None]:
    body = cast(AsyncGenerator[str | bytes, None], body_iterator)
    while True:
        chunk = await anext(body)
        found = marker.encode() in chunk if isinstance(chunk, bytes) else marker in chunk
        if found:
            return body


async def post_compat_surface(
    case: ApiRuntimeCase,
    request: CompatRequest,
) -> Response:
    headers = {"Authorization": f"Bearer {request.cleartext_key}"}
    match request.surface:
        case "native_stream":
            return await case.client.post(
                "/v1/runs/stream",
                headers=headers,
                json={
                    "agent_id": request.deployment_public_id,
                    "input": {"messages": [{"role": "user", "content": "Hello"}]},
                },
            )
        case "openai_stream":
            return await case.client.post(
                "/v1/chat/completions",
                headers=headers,
                json={
                    "model": request.deployment_public_id,
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
            )
        case "dify_blocking":
            return await case.client.post(
                f"/v1/agents/{request.deployment_public_id}/chat-messages",
                headers=headers,
                json={"query": "Hello", "response_mode": "blocking"},
            )
        case "thread_wait":
            thread = await case.client.post(
                "/v1/threads",
                headers=headers,
                json={"agent_id": request.deployment_public_id},
            )
            assert thread.status_code == 201
            return await case.client.post(
                f"/v1/threads/{thread.json()['id']}/runs/wait",
                headers=headers,
                json={
                    "agent_id": request.deployment_public_id,
                    "input": {"messages": [{"role": "user", "content": "Hello"}]},
                },
            )
        case _:
            assert_never(request.surface)
