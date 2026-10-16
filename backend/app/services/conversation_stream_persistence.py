"""Stream context, trace, and event persistence extracted from ``conversation_stream_service``."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runtime.event_broker import registry as broker_registry
from app.agent_runtime.runtime_config import AgentConfig
from app.authz.org_context import OrgContext
from app.config import settings
from app.dependencies import CurrentUser
from app.models.conversation import Conversation
from app.models.conversation_run import ConversationRun, utc_now_naive
from app.observability.langfuse import LangfuseTraceRecord
from app.services import trace_storage
from app.services.artifact_service import (
    ArtifactDeltaRecorder,
    ArtifactRuntimeContext,
    finalize_artifacts_for_run,
    link_artifacts_to_messages,
)
from app.services.conversation_stream_protocol import StreamCtx, stream_attr


def prepare_stream_context(
    conversation_id: uuid.UUID,
    *,
    run_id: str | None = None,
) -> StreamCtx:
    broker_registry.close_for_conversation(str(conversation_id))
    resolved_run_id = run_id or str(uuid.uuid4())
    broker = broker_registry.get_or_create(resolved_run_id, conversation_id=str(conversation_id))
    persist_cb = build_persist_callback(conversation_id, resolved_run_id)
    return StreamCtx(
        run_id=resolved_run_id,
        broker=broker,
        persist_cb=persist_cb,
        trace_sink=[],
        msg_id_sink=[],
        error_sink=[],
        langfuse_sink=[],
    )


def build_artifact_recorder(
    *,
    conversation_id: uuid.UUID,
    cfg: AgentConfig,
    user: CurrentUser,
    run_id: str,
) -> ArtifactDeltaRecorder | None:
    if not cfg.agent_id:
        return None
    return ArtifactDeltaRecorder(
        session_factory=cast(async_sessionmaker[AsyncSession], stream_attr("async_session")),
        context=ArtifactRuntimeContext(
            conversation_id=conversation_id,
            org_context=cfg.org_context,
            agent_id=uuid.UUID(cfg.agent_id),
            assistant_msg_id=run_id,
            output_dir=(Path(settings.conversation_output_dir) / str(conversation_id)).resolve(),
            branch_checkpoint_id=cfg.checkpoint_id,
        ),
    )


def build_persist_callback(
    conversation_id: uuid.UUID, run_id: str
) -> Callable[[list[dict[str, Any]]], Awaitable[None]]:
    # BE-P5(d): run-scoped persisted-event-id 캐시. append_events 는 partial
    # flush 마다 누적 chunk 전체 id 를 재 SELECT 했다(긴 턴에서 O(T²/64)).
    # 첫 flush 에서 DB 시드 후 증분 유지한다. 불변식: 캐시 ⊆ DB — commit
    # 성공분만 반영하고, 실패 시 None 으로 리셋해 재시도 chunk 가 DB 재로드
    # 경로로 dedup 되게 한다 (캐시가 DB 를 앞서면 재시도 이벤트가 유실된다).
    seen_event_ids: set[str] | None = None

    async def _callback(events_chunk: list[dict[str, Any]]) -> None:
        nonlocal seen_event_ids
        if not events_chunk:
            return
        try:
            async with stream_attr("async_session")() as session:
                if seen_event_ids is None:
                    seen_event_ids = await trace_storage.load_persisted_event_ids(
                        session, assistant_msg_id=run_id
                    )
                known = seen_event_ids
                await trace_storage.append_events(
                    session,
                    conversation_id=conversation_id,
                    assistant_msg_id=run_id,
                    events_chunk=events_chunk,
                    status="streaming",
                    known_event_ids=known,
                )
                last_id = events_chunk[-1].get("id")
                if isinstance(last_id, str) and last_id:
                    try:
                        run_uuid = uuid.UUID(run_id)
                    except ValueError:
                        run_uuid = None
                    if run_uuid is not None:
                        run = await session.get(ConversationRun, run_uuid)
                        if run is not None:
                            run.last_event_id = last_id
                            if run.is_active:
                                run.heartbeat_at = utc_now_naive()
                await session.commit()
        except Exception:
            seen_event_ids = None
            raise
        # commit 성공 후에만 캐시 갱신 — chunk id 는 known 과의 차집합이
        # 곧 방금 persist 된 id 셋이므로 그대로 합류한다 (idempotent).
        known.update(
            event_id
            for evt in events_chunk
            if isinstance(event_id := evt.get("id"), str) and event_id
        )

    return _callback


async def finalize_trace(
    conversation_id: uuid.UUID,
    run_id: str,
    trace_sink: list[dict[str, Any]],
    msg_id_sink: list[str],
    langfuse_sink: list[LangfuseTraceRecord],
    *,
    success: bool,
    status: trace_storage.TraceStatus | None = None,
    run_status: str | None = None,
) -> None:
    final_status: trace_storage.TraceStatus = status or ("completed" if success else "failed")
    trace_record = langfuse_sink[0] if langfuse_sink else None
    async with stream_attr("async_session")() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            return
        artifact_context = OrgContext(
            user_id=conversation.user_id,
            org_id=conversation.org_id,
            tenant_id=conversation.tenant_id,
        )
        finalized = await trace_storage.finalize_turn(
            session,
            assistant_msg_id=run_id,
            status=final_status,
            raw_msg_ids=msg_id_sink,
            conversation_id=conversation_id,
            external_trace_provider=trace_record.provider if trace_record else None,
            external_trace_id=trace_record.trace_id if trace_record else None,
            external_trace_url=trace_record.trace_url if trace_record else None,
        )
        if finalized is None and trace_sink:
            finalized = await trace_storage.record_turn(
                session,
                conversation_id=conversation_id,
                events=trace_sink,
                raw_msg_ids=msg_id_sink,
                status=final_status,
                external_trace_provider=trace_record.provider if trace_record else None,
                external_trace_id=trace_record.trace_id if trace_record else None,
                external_trace_url=trace_record.trace_url if trace_record else None,
            )
        if finalized is not None and finalized.linked_message_ids:
            await link_artifacts_to_messages(
                session,
                context=artifact_context,
                conversation_id=conversation_id,
                assistant_msg_id=run_id,
                linked_message_ids=[str(message_id) for message_id in finalized.linked_message_ids],
            )
        if run_status is not None:
            await finalize_artifacts_for_run(
                session,
                context=artifact_context,
                conversation_id=conversation_id,
                assistant_msg_id=run_id,
                run_status=run_status,
            )
        await session.commit()
