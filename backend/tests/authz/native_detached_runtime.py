"""Typed native queue lanes and the bounded provider boundary for concurrent workers."""

import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypedDict, Unpack
from uuid import UUID

import anyio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runtime.run_metrics import RunMetricsAccumulator
from app.agent_runtime.runtime_config import AgentConfig
from app.agent_runtime.streaming import StreamErrorRecord
from app.auth.cookies import ACTIVE_ORG_COOKIE
from app.authz.org_context import OrgContext
from app.models.conversation_run_input import JsonValue
from app.observability.langfuse import LangfuseTraceRecord
from app.schemas.memory import MemoryRecordCreate
from app.services import conversation_run_queue_service as queue
from app.services import memory_service
from app.services.artifact_service import ArtifactDeltaRecorder
from app.services.conversation_run_terminal_delivery import OwnerTerminalDelivery
from tests.authz.test_registration_policy_integration import SignupApp


@dataclass(frozen=True, slots=True)
class DetachedLane:
    context: OrgContext
    agent_id: UUID
    conversation_id: UUID
    input_id: UUID
    run_id: UUID


def scope_headers(stack: SignupApp, context: OrgContext) -> dict[str, str]:
    csrf = stack.client.cookies.get("moldy_csrf")
    assert csrf is not None
    parts = [
        f"{name}={value}"
        for name, value in stack.client.cookies.items()
        if name != ACTIVE_ORG_COOKIE
    ]
    parts.append(f"{ACTIVE_ORG_COOKIE}={context.org_id}")
    return {"Cookie": "; ".join(parts), "X-CSRF-Token": csrf}


async def create_lane(stack: SignupApp, context: OrgContext, model_id: UUID) -> DetachedLane:
    agent = await stack.client.post(
        "/api/agents",
        headers=scope_headers(stack, context),
        json={
            "name": "Detached fixture",
            "system_prompt": "Fixture",
            "model_id": str(model_id),
        },
    )
    assert agent.status_code == 201, agent.text
    conversation = await stack.client.post(
        f"/api/agents/{agent.json()['id']}/conversations",
        headers=scope_headers(stack, context),
        json={"title": "Detached fixture"},
    )
    assert conversation.status_code == 201, conversation.text
    conversation_id = UUID(conversation.json()["id"])
    async with stack.sessions() as db:
        queued = await queue.enqueue_input(
            db,
            context=context,
            conversation_id=conversation_id,
            client_request_id=f"concurrent-{context.org_id}",
            source="chat",
            input_payload={"content": "Fixture"},
            attachment_ids=[],
            checkpoint_id=None,
            priority=0,
        )
        await db.commit()
        claimed = await queue.claim_next_input(db, conversation_id=conversation_id, context=context)
        assert claimed is not None
        await db.commit()
        return DetachedLane(
            context, UUID(agent.json()["id"]), conversation_id, queued.id, claimed.run.id
        )


class ProviderArguments(TypedDict):
    moldy_source: str
    trace_sink: list[dict[str, JsonValue]]
    msg_id_sink: list[str]
    error_sink: list[StreamErrorRecord]
    broker: OwnerTerminalDelivery
    persist_callback: Callable[[list[dict[str, JsonValue]]], Awaitable[None]]
    run_id: str
    langfuse_sink: list[LangfuseTraceRecord]
    run_metrics: RunMetricsAccumulator
    artifact_recorder: ArtifactDeltaRecorder | None


@dataclass(slots=True)
class NativeProviderBoundary:
    """Mutable synchronization/observations; only the provider boundary is replaced."""

    sessions: async_sessionmaker[AsyncSession]
    all_started: anyio.Event = field(default_factory=anyio.Event)
    release: anyio.Event = field(default_factory=anyio.Event)
    observed: dict[str, OrgContext] = field(default_factory=dict)

    async def stream(
        self, cfg: AgentConfig, _payload: dict[str, JsonValue], **kwargs: Unpack[ProviderArguments]
    ) -> AsyncGenerator[str, None]:
        self.observed[cfg.thread_id] = cfg.org_context
        if len(self.observed) == 2:
            self.all_started.set()
        with anyio.fail_after(25):
            await self.release.wait()
        marker = str(cfg.org_context.org_id)
        recorder = kwargs["artifact_recorder"]
        assert recorder is not None and recorder.context.org_context == cfg.org_context
        await recorder.prepare()
        recorder.context.output_dir.mkdir(parents=True, exist_ok=True)
        (recorder.context.output_dir / "result.txt").write_text(marker, encoding="utf-8")
        files = await recorder.collect_after_tool_result(
            tool_name="write_file", tool_call_id="fixture"
        )
        assert len(files) == 1
        async with self.sessions() as db:
            saved = await memory_service.create_memory_record(
                db,
                context=cfg.org_context,
                payload=MemoryRecordCreate(
                    scope="user", content=marker, source_conversation_id=UUID(cfg.thread_id)
                ),
            )
            assert saved is not None
            await db.commit()
        run_id = kwargs["run_id"]
        events: list[dict[str, JsonValue]] = [
            {
                "id": f"{run_id}-1",
                "event": "message_start",
                "data": {"id": run_id, "role": "assistant"},
            },
            {"id": f"{run_id}-2", "event": "content_delta", "data": {"delta": marker}},
            {"id": f"{run_id}-3", "event": "message_end", "data": {"content": marker, "usage": {}}},
        ]
        kwargs["trace_sink"].extend(events)
        await kwargs["persist_callback"](events)
        for event in events:
            data = json.dumps(event["data"])
            yield f"id: {event['id']}\nevent: {event['event']}\ndata: {data}\n\n"
