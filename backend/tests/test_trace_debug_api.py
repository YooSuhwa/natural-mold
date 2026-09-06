from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun
from app.models.model import Model
from app.models.user import User
from app.services import trace_debug_service, trace_storage
from tests.conftest import TEST_USER_ID, TestSession


async def _seed_conversation(*, owner_id: uuid.UUID = TEST_USER_ID) -> uuid.UUID:
    async with TestSession() as db:
        db.add(User(id=owner_id, email=f"{owner_id}@test.com", name="Trace Debugger"))
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=owner_id,
            name="Trace Debugger Agent",
            description=None,
            system_prompt="You debug traces.",
            model_id=model.id,
            status="active",
        )
        db.add(agent)
        await db.flush()
        conv = Conversation(agent_id=agent.id, title="Trace Debugger")
        db.add(conv)
        await db.commit()
        return conv.id


def _events(msg_id: str, *, failed: bool = False, secret: str | None = None) -> list[dict]:
    tool_args = {"query": "moldy"}
    input_payload: dict[str, list[dict[str, str]] | dict[str, str]] = {
        "messages": [{"role": "user", "content": "debug this trace"}]
    }
    if secret is not None:
        tool_args["api_key"] = secret
        input_payload["headers"] = {
            "Authorization": f"Bearer {secret}",
            "Cookie": f"moldy_at={secret}",
            "User-Agent": "safe-agent",
        }
    body = [
        {
            "id": f"{msg_id}-1",
            "event": "message_start",
            "data": {
                "id": msg_id,
                "input": input_payload,
            },
        },
        {
            "id": f"{msg_id}-2",
            "event": "tool_call_start",
            "data": {"name": "web_search", "args": tool_args},
        },
    ]
    if failed:
        body.append(
            {
                "id": f"{msg_id}-3",
                "event": "error",
                "data": {"message": "provider failed"},
            }
        )
    body.append(
        {
            "id": f"{msg_id}-4",
            "event": "message_end",
            "data": {
                "content": "done",
                "usage": {"prompt_tokens": 3, "completion_tokens": 5},
                "status": "failed" if failed else "completed",
            },
        }
    )
    return body


def _legacy_offload_paths() -> tuple[str, str, str, str]:
    """Return persisted path shapes that must never cross trace egress."""
    return (
        "/conversation_history/session_0123456789abcdef0123456789abcdef.md",
        "/large_tool_results/tool-call.json",
        (
            "/.moldy-offload/owner/conversation/actor/"
            "conversation_history/session_0123456789abcdef0123456789abcdef.md"
        ),
        (
            "/tmp/moldy/.moldy-internal/offload/spill/owner/run/actor/"
            "large_tool_results/tool-call.json"
        ),
    )


def _assert_offload_paths_are_projected(rendered: str, paths: tuple[str, str, str, str]) -> None:
    """Assert public trace data contains only opaque history/spill identifiers."""
    for path in paths:
        assert path not in rendered
    assert "/conversation_history/" not in rendered
    assert "/large_tool_results/" not in rendered
    assert "/.moldy-offload/" not in rendered
    assert "/.moldy-internal/offload/" not in rendered
    assert "history_" in rendered
    assert "spill_" not in rendered
    assert "internal_reference_redacted" in rendered
    assert "file_path" not in rendered


async def _seed_trace(
    conversation_id: uuid.UUID,
    *,
    run_id: str = "run-debugger",
    trace_id: str = "lf-trace-debugger",
    failed: bool = False,
    secret: str | None = None,
) -> None:
    async with TestSession() as db:
        await trace_storage.record_turn(
            db,
            conversation_id=conversation_id,
            events=_events(run_id, failed=failed, secret=secret),
            status="failed" if failed else "completed",
            external_trace_provider="langfuse",
            external_trace_id=trace_id,
            external_trace_url=f"https://langfuse.local/project/moldy/traces/{trace_id}",
        )
        await db.commit()


async def _seed_run_status(
    conversation_id: uuid.UUID,
    *,
    run_id: uuid.UUID,
    status: str,
) -> None:
    async with TestSession() as db:
        conv = await db.get(Conversation, conversation_id)
        assert conv is not None
        db.add(
            ConversationRun(
                id=run_id,
                conversation_id=conversation_id,
                agent_id=conv.agent_id,
                user_id=TEST_USER_ID,
                source="chat",
                status=status,
                is_active=False,
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_debug_traces_requires_auth(raw_client: AsyncClient) -> None:
    conv_id = await _seed_conversation()
    await _seed_trace(conv_id)

    response = await raw_client.get(f"/api/conversations/{conv_id}/debug/traces")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_debug_traces_hides_other_users_conversation(client: AsyncClient) -> None:
    conv_id = await _seed_conversation(owner_id=uuid.uuid4())
    await _seed_trace(conv_id)

    response = await client.get(f"/api/conversations/{conv_id}/debug/traces")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_debug_traces_returns_correlated_turn_summaries(client: AsyncClient) -> None:
    conv_id = await _seed_conversation()
    await _seed_trace(conv_id, run_id="run-debugger", trace_id="lf-trace-debugger")

    response = await client.get(f"/api/conversations/{conv_id}/debug/traces")

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == str(conv_id)
    assert body["traces"][0]["trace_id"] == "lf-trace-debugger"
    assert body["traces"][0]["provider"] == "langfuse"
    assert body["traces"][0]["moldy_run_id"] == "run-debugger"
    assert body["traces"][0]["status"] == "completed"
    assert body["traces"][0]["total_tokens"] == 8
    assert body["traces"][0]["langfuse_url"].endswith("/lf-trace-debugger")


@pytest.mark.asyncio
async def test_debug_traces_use_conversation_run_product_status(client: AsyncClient) -> None:
    conv_id = await _seed_conversation()
    canceled_run_id = uuid.uuid4()
    stale_run_id = uuid.uuid4()
    await _seed_trace(
        conv_id,
        run_id=str(canceled_run_id),
        trace_id="lf-trace-canceled",
        failed=False,
    )
    await _seed_trace(
        conv_id,
        run_id=str(stale_run_id),
        trace_id="lf-trace-stale",
        failed=True,
    )
    await _seed_run_status(conv_id, run_id=canceled_run_id, status="canceled")
    await _seed_run_status(conv_id, run_id=stale_run_id, status="stale")

    response = await client.get(f"/api/conversations/{conv_id}/debug/traces")

    assert response.status_code == 200
    by_trace = {item["trace_id"]: item for item in response.json()["traces"]}
    assert by_trace["lf-trace-canceled"]["status"] == "canceled"
    assert by_trace["lf-trace-stale"]["status"] == "stale"


@pytest.mark.asyncio
async def test_debug_trace_detail_falls_back_to_message_events(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conv_id = await _seed_conversation()
    await _seed_trace(
        conv_id,
        run_id="run-failed",
        trace_id="lf-trace-failed",
        failed=True,
    )

    async def _fake_fetch(*_args, **_kwargs):
        return [], "langfuse unavailable"

    monkeypatch.setattr(
        "app.services.trace_debug_service.is_langfuse_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.trace_debug_service.fetch_langfuse_observations",
        _fake_fetch,
    )

    response = await client.get(f"/api/conversations/{conv_id}/debug/traces/lf-trace-failed")

    assert response.status_code == 200
    body = response.json()
    assert body["trace"]["trace_id"] == "lf-trace-failed"
    assert body["trace"]["status"] == "failed"
    assert body["fallback_reason"] == "langfuse unavailable"
    assert body["spans"][0]["name"] == "Moldy assistant turn"
    assert body["spans"][0]["input"] == {
        "messages": [{"role": "user", "content": "debug this trace"}]
    }
    assert any(span["kind"] == "error" for span in body["spans"])


@pytest.mark.asyncio
async def test_debug_trace_detail_projects_legacy_offload_paths_without_mutating_events(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback spans apply offload projection before the normal secret redaction."""
    conv_id = await _seed_conversation()
    message_id = "legacy-debug-offload"
    trace_id = "lf-trace-legacy-debug-offload"
    history_path, spill_path, virtual_path, physical_path = _legacy_offload_paths()
    configured_secret = "debug-trace-configured-secret-42"
    events = _events(message_id)
    events[0]["data"]["input"] = {
        "history": history_path,
        "spill": spill_path,
        "virtual": virtual_path,
        "physical": physical_path,
        "note": f"path={history_path}; credential={configured_secret}",
        "_summarization_event": {"file_path": history_path},
    }
    events[0]["data"]["source"] = physical_path
    events[1]["data"]["args"] = {"result_path": physical_path}
    events[1]["id"] = f"{history_path}:span-id:{configured_secret}"
    events[1]["data"]["name"] = f"{history_path}:span-name:{configured_secret}"

    async with TestSession() as db:
        await trace_storage.record_turn(
            db,
            conversation_id=conv_id,
            events=events,
            external_trace_provider="langfuse",
            external_trace_id=trace_id,
        )
        await db.commit()

    async def _fake_fetch(*_args, **_kwargs):
        return [], "langfuse unavailable"

    monkeypatch.setattr("app.services.trace_debug_service.is_langfuse_enabled", lambda: True)
    monkeypatch.setattr("app.services.trace_debug_service.fetch_langfuse_observations", _fake_fetch)

    with patch(
        "app.services.chat.secrets.collect_conversation_secret_values",
        new=AsyncMock(return_value={configured_secret}),
    ):
        list_response = await client.get(f"/api/conversations/{conv_id}/debug/traces")
        response = await client.get(f"/api/conversations/{conv_id}/debug/traces/{trace_id}")

    assert list_response.status_code == 200
    assert configured_secret not in repr(list_response.json())
    for path in _legacy_offload_paths():
        assert path not in repr(list_response.json())
    assert list_response.json()["traces"][0]["source"] == "internal_reference_redacted"
    assert list_response.json()["traces"][0]["name"] == "agent.internal_reference_redacted"
    assert response.status_code == 200
    assert configured_secret not in repr(response.json())
    _assert_offload_paths_are_projected(repr(response.json()), _legacy_offload_paths())
    tool_span = next(span for span in response.json()["spans"] if span["kind"] == "tool")
    assert "history_" in tool_span["id"]
    assert "<redacted>" in tool_span["id"]
    assert "history_" in tool_span["name"]
    assert "<redacted>" in tool_span["name"]

    async with TestSession() as db:
        stored = await trace_storage.get_trace_by_msg_id(db, message_id)
        assert stored is not None
        assert stored.events == events


def test_debug_observation_spans_project_legacy_offload_paths_without_mutating_input() -> None:
    """Langfuse observation projection copies the source row before trace egress."""
    history_path, spill_path, virtual_path, physical_path = _legacy_offload_paths()
    rows = [
        {
            "id": "legacy-offload-observation",
            "input": {
                "history": history_path,
                "spill": spill_path,
                "virtual": virtual_path,
                "physical": physical_path,
                "_summarization_event": {"file_path": history_path},
            },
            "output": physical_path,
            "metadata": {"source_path": spill_path},
        }
    ]

    spans = trace_debug_service.spans_from_observations(rows)

    _assert_offload_paths_are_projected(
        repr([span.model_dump() for span in spans]),
        _legacy_offload_paths(),
    )
    assert rows[0]["input"]["_summarization_event"]["file_path"] == history_path


@pytest.mark.asyncio
async def test_debug_trace_detail_redacts_sensitive_message_event_payloads(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "moldy-e2e-secret-token-should-not-persist"
    conv_id = await _seed_conversation()
    await _seed_trace(conv_id, trace_id="lf-trace-secret-fallback", secret=secret)

    async def _fake_fetch(*_args, **_kwargs):
        return [], "langfuse unavailable"

    monkeypatch.setattr(
        "app.services.trace_debug_service.is_langfuse_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.trace_debug_service.fetch_langfuse_observations",
        _fake_fetch,
    )

    response = await client.get(
        f"/api/conversations/{conv_id}/debug/traces/lf-trace-secret-fallback"
    )

    assert response.status_code == 200
    body = response.json()
    assert secret not in repr(body)
    assert body["spans"][0]["input"]["headers"] == {
        "Authorization": "<redacted>",
        "Cookie": "<redacted>",
        "User-Agent": "safe-agent",
    }
    tool_span = next(
        span for span in body["spans"] if span["metadata"].get("event") == "tool_call_start"
    )
    assert tool_span["input"] == {"query": "moldy", "api_key": "<redacted>"}
    assert tool_span["metadata"]["data"]["args"] == {
        "query": "moldy",
        "api_key": "<redacted>",
    }


@pytest.mark.asyncio
async def test_debug_trace_detail_roots_orphan_langfuse_observations(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conv_id = await _seed_conversation()
    await _seed_trace(conv_id, trace_id="lf-trace-orphan")

    async def _fake_fetch(*_args, **_kwargs):
        return [
            {
                "id": "obs-child",
                "parentObservationId": "missing-parent",
                "name": "ChatOpenAI",
                "type": "GENERATION",
                "level": "DEFAULT",
                "input": {"messages": [{"role": "user", "content": "debug this trace"}]},
            }
        ], None

    monkeypatch.setattr(
        "app.services.trace_debug_service.is_langfuse_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.trace_debug_service.fetch_langfuse_observations",
        _fake_fetch,
    )

    response = await client.get(f"/api/conversations/{conv_id}/debug/traces/lf-trace-orphan")

    assert response.status_code == 200
    body = response.json()
    assert body["spans"][0]["id"] == "obs-child"
    assert body["spans"][0]["parent_id"] is None
    assert body["spans"][0]["input"] == {
        "messages": [{"role": "user", "content": "debug this trace"}]
    }


@pytest.mark.asyncio
async def test_debug_trace_detail_redacts_sensitive_langfuse_observations(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "moldy-e2e-secret-token-should-not-persist"
    conv_id = await _seed_conversation()
    await _seed_trace(conv_id, trace_id="lf-trace-secret-observation")

    async def _fake_fetch(*_args, **_kwargs):
        return [
            {
                "id": "obs-secret",
                "name": "execute_in_skill",
                "type": "SPAN",
                "level": "DEFAULT",
                "input": {
                    "api_key": secret,
                    "headers": {
                        "Authorization": f"Bearer {secret}",
                        "Cookie": f"moldy_at={secret}",
                        "User-Agent": "safe-agent",
                    },
                    "query": "safe",
                },
                "output": f"Authorization: Bearer {secret}",
                "metadata": {
                    "api_key": secret,
                    "headers": {
                        "Set-Cookie": f"moldy_rt={secret}",
                        "User-Agent": "safe-agent",
                    },
                    "note": "safe",
                },
                "usageDetails": {"total_tokens": 9},
            }
        ], None

    monkeypatch.setattr(
        "app.services.trace_debug_service.is_langfuse_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "app.services.trace_debug_service.fetch_langfuse_observations",
        _fake_fetch,
    )

    response = await client.get(
        f"/api/conversations/{conv_id}/debug/traces/lf-trace-secret-observation"
    )

    assert response.status_code == 200
    body = response.json()
    assert secret not in repr(body)
    assert body["raw"][0]["input"] == {
        "api_key": "<redacted>",
        "headers": {
            "Authorization": "<redacted>",
            "Cookie": "<redacted>",
            "User-Agent": "safe-agent",
        },
        "query": "safe",
    }
    assert body["spans"][0]["input"] == {
        "api_key": "<redacted>",
        "headers": {
            "Authorization": "<redacted>",
            "Cookie": "<redacted>",
            "User-Agent": "safe-agent",
        },
        "query": "safe",
    }
    assert body["spans"][0]["metadata"]["api_key"] == "<redacted>"
    assert body["spans"][0]["metadata"]["headers"] == {
        "Set-Cookie": "<redacted>",
        "User-Agent": "safe-agent",
    }
    assert body["spans"][0]["metadata"]["usage"] == {"total_tokens": 9}
