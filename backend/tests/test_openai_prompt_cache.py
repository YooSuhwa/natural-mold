"""Inspect the final SDK request on isolated HTTP transports. No model account is used."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, nullcontext
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from langchain_core.language_models import BaseChatModel

from app.agent_runtime import lease_owned_model_client, model_provider_builders
from app.agent_runtime.lease_model_factory import LeaseModelSpec, create_lease_chat_model
from app.agent_runtime.model_factory import clear_model_cache, create_chat_model
from app.agent_runtime.openai_prompt_cache import conversation_prompt_cache
from app.authz.org_context import OrgContext
from app.config import settings
from scripts.compare_prompt_cache import Arm, Observation, compare
from tests.authz.lease_provider_wire import Wire, guard_for

type Requests = list[tuple[httpx.URL, dict[str, Any]]]


@pytest.fixture
def requests(monkeypatch: pytest.MonkeyPatch) -> Iterator[Requests]:
    captured: Requests = []

    def respond(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        captured.append((req.url, body))
        if body.get("stream"):
            response_id = f"chatcmpl-fixture-{len(captured)}"
            chunk = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": body["model"],
            }
            # These are synthetic counts, including automatic hits in the omitted arm.
            returned_usage = {
                "prompt_tokens": 2048,
                "completion_tokens": 1,
                "total_tokens": 2049,
                "prompt_tokens_details": {
                    "cached_tokens": 1536 if "prompt_cache_key" in body else 1024
                },
            }
            frames = [
                {**chunk, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]},
                {**chunk, "choices": [{"index": 0, "delta": {"content": "ok"}}]},
                {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {**chunk, "choices": [], "usage": returned_usage},
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="".join(f"data: {json.dumps(frame)}\n\n" for frame in frames)
                + "data: [DONE]\n\n",
            )
        if req.url.path == "/v1/responses":
            return httpx.Response(
                200,
                json={
                    "id": "resp_test",
                    "object": "response",
                    "created_at": 1,
                    "model": "gpt-4o-mini",
                    "status": "completed",
                    "parallel_tool_calls": False,
                    "output": [
                        {
                            "id": "msg_test",
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                        }
                    ],
                    "tools": [],
                    "tool_choice": "auto",
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": body.get("prompt_cache_key", "ok"),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 2048,
                    "completion_tokens": 1,
                    "total_tokens": 2049,
                    "prompt_tokens_details": {"cached_tokens": 1024},
                },
            },
        )

    wire = httpx.MockTransport(respond)
    clear_model_cache()
    monkeypatch.setattr(
        model_provider_builders,
        "outbound_http_client",
        lambda **_: httpx.AsyncClient(transport=wire),
    )
    monkeypatch.setattr(
        model_provider_builders,
        "outbound_sync_http_client",
        lambda **_: httpx.Client(transport=wire),
    )
    monkeypatch.setattr(lease_owned_model_client, "_new_async_transport", lambda _: wire)
    monkeypatch.setattr(lease_owned_model_client, "_new_sync_transport", lambda _: wire)
    yield captured
    clear_model_cache()


@asynccontextmanager
async def model_for(
    leased: bool, provider: str = "openai", base: str | None = None, **params: Any
) -> AsyncIterator[BaseChatModel]:
    if leased:
        owned = create_lease_chat_model(
            LeaseModelSpec(provider, "gpt-4o-mini", "test", base, params), guard=guard_for(Wire())
        )
        try:
            yield owned.model
        finally:
            await owned.aclose()
    else:
        yield create_chat_model(provider, "gpt-4o-mini", "test", base, **params)


@pytest.mark.parametrize("leased", [False, True])
async def test_same_wire_input_concurrent_scopes_and_opt_out(
    leased: bool, requests: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = uuid4()
    orgs = [OrgContext(uuid4(), tenant, uuid4()) for _ in range(2)]
    monkeypatch.setattr(settings, "openai_prompt_cache_org_ids", frozenset(o.org_id for o in orgs))
    chats = [uuid4(), uuid4()]
    async with model_for(leased) as model:
        # Missing scope and opting in each use exactly the same model and messages.
        off = await model.ainvoke("same input")
        assert (
            off.usage_metadata
            and off.usage_metadata.get("input_token_details", {}).get("cache_read") == 1024
        )
        baseline = requests[-1][1]
        assert "prompt_cache_key" not in baseline

        async def keys_for(org: OrgContext, chat: UUID) -> list[str]:
            keys = []
            for _ in range(2):
                with conversation_prompt_cache(org, chat, run_path="chat"):
                    # Distinguish concurrent results by task, without changing the input body.
                    await asyncio.sleep(0)
                    response = await model.ainvoke("same input")
                    assert response.usage_metadata == off.usage_metadata
                    # The mock provider echoes the wire key to its own caller.
                    assert isinstance(response.content, str)
                    keys.append(response.content)
            return keys

        per_chat = await asyncio.gather(*(keys_for(org, chat) for org in orgs for chat in chats))
        assert all(first == second for first, second in per_chat)
        keys = {first for first, _ in per_chat}
        assert len(keys) == 4
        assert sorted(body["prompt_cache_key"] for _, body in requests[1:]) == sorted(
            list(keys) * 2
        )
        assert all(
            {k: v for k, v in body.items() if k != "prompt_cache_key"} == baseline
            for _, body in requests[1:]
        )
        assert all(len(key) == 64 and set(key) <= set("0123456789abcdef") for key in keys)
        for org in orgs:
            assert all(org.org_id.hex not in key and tenant.hex not in key for key in keys)
        assert all(chat.hex not in key for chat in chats for key in keys)
        for path, org in [
            ("api", orgs[0]),
            ("trigger", orgs[0]),
            ("chat", OrgContext(uuid4(), tenant, uuid4())),
        ]:
            with conversation_prompt_cache(org, chats[0], run_path=path):
                await model.ainvoke("same input")
            assert requests[-1][1] == baseline
        await model.ainvoke("same input")
        assert requests[-1][1] == baseline


@pytest.mark.parametrize("leased", [False, True])
@pytest.mark.parametrize(
    ("provider", "base"),
    [
        ("openai", "https://company.invalid/v1"),
        ("openai", "http://api.openai.com/v1"),
        ("openai", "https://api.openai.com.gateway.invalid/v1"),
        ("openai", "https://api.openai.com:8443/v1"),
        ("openai_compatible", "https://api.openai.com/v1"),
        ("openrouter", None),
        ("custom", "https://company.invalid/v1"),
    ],
)
async def test_nonofficial_route_gets_no_generated_or_configured_key(
    provider: str,
    base: str | None,
    leased: bool,
    requests: Requests,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    org = OrgContext(uuid4(), uuid4(), uuid4())
    monkeypatch.setattr(settings, "openai_prompt_cache_org_ids", frozenset({org.org_id}))
    async with model_for(leased, provider, base, prompt_cache_key="configured") as model:
        with conversation_prompt_cache(org, uuid4(), run_path="chat"):
            await model.ainvoke("query")
    assert "prompt_cache_key" not in requests[-1][1]


@pytest.mark.parametrize("leased", [False, True])
async def test_explicit_key_precedence_and_official_sdk_destination(
    leased: bool, requests: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = OrgContext(uuid4(), uuid4(), uuid4())
    monkeypatch.setattr(settings, "openai_prompt_cache_org_ids", frozenset({org.org_id}))
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.invalid/v1")
    async with model_for(leased, prompt_cache_key="configured") as model:
        with conversation_prompt_cache(org, uuid4(), run_path="chat"):
            await model.ainvoke("query")
            await model.ainvoke("query", prompt_cache_key="caller")
            await model.ainvoke("query", extra_body={"prompt_cache_key": "extra-body"})
    assert [body["prompt_cache_key"] for _, body in requests] == [
        "configured",
        "caller",
        "extra-body",
    ]
    assert all(url.host == "api.openai.com" for url, _ in requests)


async def test_lease_responses_api_uses_same_scope(
    requests: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = OrgContext(uuid4(), uuid4(), uuid4())
    monkeypatch.setattr(settings, "openai_prompt_cache_org_ids", frozenset({org.org_id}))
    async with model_for(True, use_responses_api=True) as model:
        with conversation_prompt_cache(org, uuid4(), run_path="chat"):
            await model.ainvoke("query")
    assert requests[-1][0].path == "/v1/responses"
    assert len(requests[-1][1]["prompt_cache_key"]) == 64


@pytest.mark.parametrize("leased", [False, True])
async def test_unrecognized_configured_key_is_ignored_when_routing_off(
    leased: bool, requests: Requests
) -> None:
    async with model_for(leased, prompt_cache_key={"old": "unused setting"}) as model:
        await model.ainvoke("query")
    assert "prompt_cache_key" not in requests[-1][1]


async def test_paired_sdk_streams_feed_cost_comparison_without_claiming_provider_savings(
    requests: Requests, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = OrgContext(uuid4(), uuid4(), uuid4())
    chat = uuid4()
    monkeypatch.setattr(settings, "openai_prompt_cache_org_ids", frozenset({org.org_id}))
    observations = []
    async with model_for(False) as model:
        for sequence in range(1, 31):
            arms: tuple[Arm, Arm] = ("key_omitted", "key_supplied")
            for arm in arms if sequence % 2 else reversed(arms):
                scope = (
                    conversation_prompt_cache(org, chat, run_path="chat")
                    if arm == "key_supplied"
                    else nullcontext()
                )
                first_token = None
                usage = None
                start = time.monotonic_ns()
                with scope:
                    async for delta in model.astream("fixed model input " * 1024):
                        if delta.text and first_token is None:
                            first_token = time.monotonic_ns()
                        if delta.usage_metadata:
                            usage = delta.usage_metadata
                assert usage is not None and first_token is not None
                body = requests[-1][1].copy()
                routed = body.pop("prompt_cache_key", None)
                assert bool(routed) == (arm == "key_supplied")
                comparable = json.dumps(body, sort_keys=True).encode()
                observations.append(
                    Observation(
                        arm=arm,
                        sequence=sequence,
                        scope="fixture-scope",
                        model=body["model"],
                        request_sha256=hashlib.sha256(comparable).hexdigest(),
                        request_id=f"chatcmpl-fixture-{len(requests)}",
                        source="fixture",
                        input_tokens=usage["input_tokens"],
                        cached_input_tokens=usage.get("input_token_details", {}).get(
                            "cache_read", 0
                        ),
                        ttft_ms=Decimal(first_token - start) / 1_000_000,
                    )
                )
    report = compare(observations, Decimal("1"), Decimal("0.5"))
    assert report["pairs"] == 30
    assert report["verdict"] == "fixture_only"
    assert report["reconciled_mean_input_cost_change_pct"] is None
    assert report["estimated_mean_input_cost_change_pct"] == Decimal(-100) / 6
