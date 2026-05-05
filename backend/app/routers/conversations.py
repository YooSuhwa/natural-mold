from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any, NamedTuple

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime import event_names
from app.agent_runtime.credential_resolution import resolve_llm_api_key_for_agent
from app.agent_runtime.event_broker import EventBroker, slice_events_after
from app.agent_runtime.event_broker import registry as broker_registry
from app.agent_runtime.executor import AgentConfig, execute_agent_stream, resume_agent_stream
from app.agent_runtime.model_factory import env_provider_keys
from app.agent_runtime.streaming import format_sse
from app.config import settings
from app.database import async_session
from app.dependencies import CurrentUser, get_current_user, get_db
from app.error_codes import (
    agent_not_found,
    conversation_not_found,
    file_not_found,
    resume_interrupt_pending,
    resume_not_found,
)
from app.models.message_event import MessageEvent
from app.models.model import Model
from app.schemas.conversation import (
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
    EditMessageRequest,
    MessageCreate,
    MessagesEnvelope,
    RegenerateMessageRequest,
    ResumeRequest,
    SwitchBranchRequest,
    TurnTraceResponse,
)
from app.services import chat_service, thread_branch_service, trace_storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["conversations"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_agent_context(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    user: CurrentUser,
    *,
    checkpoint_id: str | None = None,
) -> AgentConfig:
    """conversation + agent 조회 → AgentConfig 생성.

    send_message와 resume_message에서 공통으로 사용.
    """
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()

    agent = await chat_service.get_agent_with_tools(db, conv.agent_id, user.id)
    if not agent:
        raise agent_not_found()

    if agent.model is None:
        # Legacy data: agent's model_id points at a deleted Model row. Chat
        # cannot run without a model bound — surface a clear 422 so the UI
        # can prompt the user to re-bind in agent settings.
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422,
            detail=(
                "agent has no model bound — open agent settings and pick a "
                "model before chatting."
            ),
        )

    # Tiered: agent.llm_credential → model.default_credential_id → env fallback.
    # Lets users skip the per-agent credential pick when their model already
    # carries the right default.
    api_key = await resolve_llm_api_key_for_agent(db, agent)
    base_url = agent.model.base_url

    tools_config = await chat_service.build_tools_config(
        agent, db=db, conversation_id=str(conversation_id)
    )

    fallback_chain = await _resolve_fallback_chain(db, agent.model_fallback_list)

    return AgentConfig(
        provider=agent.model.provider,
        model_name=agent.model.model_name,
        api_key=api_key,
        base_url=base_url,
        system_prompt=chat_service.build_effective_prompt(agent),
        tools_config=tools_config,
        thread_id=str(conversation_id),
        model_params=agent.model_params,
        middleware_configs=agent.middleware_configs,
        agent_skills=chat_service.build_agent_skills(agent) or None,
        agent_id=str(agent.id),
        provider_api_keys=env_provider_keys(),
        cost_per_input_token=(
            float(agent.model.cost_per_input_token) if agent.model.cost_per_input_token else None
        ),
        cost_per_output_token=(
            float(agent.model.cost_per_output_token) if agent.model.cost_per_output_token else None
        ),
        user_id=str(agent.user_id),
        model_id=str(agent.model.id) if agent.model else None,
        llm_credential_id=(
            str(agent.llm_credential.id) if agent.llm_credential is not None else None
        ),
        model_fallback_chain=fallback_chain,
        checkpoint_id=checkpoint_id,
    )


async def _resolve_fallback_chain(
    db: AsyncSession,
    fallback_list: list[str] | None,
) -> list[dict[str, str | None]] | None:
    """Resolve agent.model_fallback_list (UUID strings) → ordered chain dicts.

    Missing rows are silently dropped — the catalog can change while an
    agent's fallback list is stale, and we don't want a deleted fallback
    breaking the runtime. Returns ``None`` when there are no resolvable
    entries so the executor skips the fallback path entirely.
    """

    if not fallback_list:
        return None
    from sqlalchemy import select

    fallback_uuids: list[uuid.UUID] = []
    for raw in fallback_list:
        try:
            fallback_uuids.append(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    if not fallback_uuids:
        return None
    result = await db.execute(select(Model).where(Model.id.in_(fallback_uuids)))
    rows = {row.id: row for row in result.scalars().all()}
    chain: list[dict[str, str | None]] = []
    for fid in fallback_uuids:
        row = rows.get(fid)
        if row is None:
            continue
        chain.append(
            {
                "provider": row.provider,
                "model_name": row.model_name,
                "base_url": row.base_url,
                "model_id": str(row.id),
            }
        )
    return chain or None


def _error_sse_pair(error_message: str) -> list[str]:
    """에러 SSE + message_end 페어를 생성."""
    from app.agent_runtime.streaming import format_sse

    return [
        format_sse(event_names.ERROR, {"message": error_message}),
        format_sse(event_names.MESSAGE_END, {"usage": {}, "content": ""}),
    ]


def _sse_response(
    generator: AsyncGenerator[str, None],
    *,
    extra_headers: dict[str, str] | None = None,
) -> StreamingResponse:
    """StreamingResponse 래퍼.

    ``extra_headers`` 는 W3-out M2의 ``X-Run-Id`` 처럼 stream-specific 헤더를
    추가하기 위한 hook. 기본 _SSE_HEADERS 와 머지된다.
    """  # noqa: D401
    headers = dict(_SSE_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    return StreamingResponse(generator, media_type="text/event-stream", headers=headers)


def _sse_handler(
    executor_fn: Callable[[], AsyncGenerator[str, None]],
    *,
    log_msg: str,
    user_msg: str,
    on_complete: Callable[[bool], Awaitable[None]] | None = None,
    run_id: str | None = None,
) -> StreamingResponse:
    """4개 stream endpoint(send/resume/edit/regenerate)가 공유하는 SSE 래퍼.

    - executor_fn: lazy factory — 매 호출마다 새 AsyncGenerator를 생성해야 함.
    - log_msg: logger.exception 메시지 (서버 사이드).
    - user_msg: 클라이언트로 전달되는 user-facing 에러 메시지.
    - on_complete: 스트림 종료 후(성공/실패 무관) 1회 호출되는 hook. W5
      trace 영속화 / W3-out M2 finalize_turn 호출에 사용. ``success`` bool
      인자는 generator가 예외 없이 끝났는지(=`completed`) 또는 예외로 SSE
      error 페어를 emit했는지(=`failed`) 를 알려준다. 실패해도 client에는
      영향 없도록 swallow + log.
    - run_id: W3-out M2 — 응답 헤더 ``X-Run-Id`` 노출. 클라이언트가 이 id를
      들고 GET ``/stream?run_id=`` 로 재연결한다.
    """

    async def generate() -> AsyncGenerator[str, None]:
        success = False
        try:
            async for chunk in executor_fn():
                yield chunk
            success = True
        except Exception:
            logger.exception(log_msg)
            for chunk in _error_sse_pair(user_msg):
                yield chunk
        finally:
            if on_complete is not None:
                try:
                    await on_complete(success)
                except Exception:
                    logger.exception("on_complete hook failed (%s)", log_msg)

    extra: dict[str, str] | None = (
        {"X-Run-Id": run_id} if run_id else None
    )
    return _sse_response(generate(), extra_headers=extra)


class _StreamCtx(NamedTuple):
    """W3-out M2 — 4개 POST 핸들러가 공통으로 만드는 streaming 컨텍스트.

    ``_prepare_stream_context`` 가 한 번에 생성, 핸들러는 ``as_stream_kwargs()``
    + ``finalize_callback()`` 두 메서드로 explicit 분해 없이 forward.
    """

    run_id: str
    broker: EventBroker
    persist_cb: Callable[[list[dict[str, Any]]], Awaitable[None]]
    trace_sink: list[dict[str, Any]]
    msg_id_sink: list[str]

    def as_stream_kwargs(self) -> dict[str, Any]:
        """``execute_agent_stream`` / ``resume_agent_stream`` 의 dual-write 채널
        kwargs 묶음. 핸들러가 5개를 매번 explicit 전달할 때의 invariant
        (``persist_cb`` 와 ``run_id`` 가 같은 ctx 에서 와야 함, broker 가 같은
        run_id 로 등록된 인스턴스여야 함) 를 한 곳에서 강제.
        """
        return {
            "trace_sink": self.trace_sink,
            "msg_id_sink": self.msg_id_sink,
            "broker": self.broker,
            "persist_callback": self.persist_cb,
            "run_id": self.run_id,
        }

    def finalize_callback(
        self, conversation_id: uuid.UUID
    ) -> Callable[[bool], Awaitable[None]]:
        """``_sse_handler.on_complete`` 에 그대로 넘길 수 있는 closure.

        4 핸들러가 같은 lambda (``lambda success: _finalize_trace(conversation
        _id, self.run_id, self.trace_sink, self.msg_id_sink, success=success)``)
        를 매번 hand-roll 하던 것을 묶음. ``conversation_id`` 만 외부에서 주입
        — 나머지는 self 에서.
        """

        async def _cb(success: bool) -> None:
            await _finalize_trace(
                conversation_id,
                self.run_id,
                self.trace_sink,
                self.msg_id_sink,
                success=success,
            )

        return _cb


def _prepare_stream_context(conversation_id: uuid.UUID) -> _StreamCtx:
    """W3-out M2 — 4 POST 핸들러 공통 셋업 (run_id + broker + sinks + persist_cb).

    핸들러는 ``ctx = _prepare_stream_context(conversation_id)`` 한 줄로 받고,
    ``ctx.as_stream_kwargs()`` 로 executor 호출 + ``ctx.finalize_callback(
    conversation_id)`` 로 ``_sse_handler.on_complete`` 마감.

    같은 conversation 의 직전 turn 이 disconnect 등으로 broker 가 미정리
    상태로 남아 있으면 즉시 회수 (M4 APScheduler GC 도래 전 ghost broker
    누적 차단). 새 turn 진입은 동시 2 turn 금지 정책의 명시 신호.
    """
    broker_registry.close_for_conversation(str(conversation_id))
    run_id = str(uuid.uuid4())
    broker = broker_registry.get_or_create(
        run_id, conversation_id=str(conversation_id)
    )
    persist_cb = _build_persist_callback(conversation_id, run_id)
    return _StreamCtx(
        run_id=run_id,
        broker=broker,
        persist_cb=persist_cb,
        trace_sink=[],
        msg_id_sink=[],
    )


def _build_persist_callback(
    conversation_id: uuid.UUID, run_id: str
) -> Callable[[list[dict[str, Any]]], Awaitable[None]]:
    """W3-out M2 — partial flush 콜백 팩토리.

    ``stream_agent_response`` 가 32 events 또는 2초마다 fire-and-forget 으로
    호출. 매 호출마다 fresh ``async_session()`` 을 열어 commit. SSE generate()
    의 request-scoped db session 과 분리되어 있어야 한다.
    """

    async def _callback(events_chunk: list[dict[str, Any]]) -> None:
        if not events_chunk:
            return
        async with async_session() as session:
            await trace_storage.append_events(
                session,
                conversation_id=conversation_id,
                assistant_msg_id=run_id,
                events_chunk=events_chunk,
                status="streaming",
            )
            await session.commit()

    return _callback


async def _finalize_trace(
    conversation_id: uuid.UUID,
    run_id: str,
    trace_sink: list[dict[str, Any]],
    msg_id_sink: list[str],
    *,
    success: bool,
) -> None:
    """W3-out M2 — 스트림 종료 후 한 번 호출되는 dual-path persist hook.

    1순위: ``finalize_turn`` 으로 partial flush로 이미 만들어진 row를
        ``status='completed' | 'failed'`` 로 마감하고 ``linked_message_ids`` 를
        부착한다.
    2순위 (fallback): 한 번도 ``append_events`` 가 호출되지 않은 경우(예: 즉시
        에러로 message_start 이전에 종료) row가 없다. trace_sink 에 모인
        이벤트로 ``record_turn`` 을 호출해 적어도 한 row를 남긴다.

    SSE generate() 의 request-scoped session 과 분리하기 위해
    ``async_session()`` 으로 새 session 을 연다.
    """
    final_status: trace_storage.TraceStatus = "completed" if success else "failed"
    async with async_session() as session:
        finalized = await trace_storage.finalize_turn(
            session,
            assistant_msg_id=run_id,
            status=final_status,
            raw_msg_ids=msg_id_sink,
            conversation_id=conversation_id,
        )
        if finalized is None and trace_sink:
            # No partial flush happened (e.g. immediate failure). Persist via
            # legacy shim so traces aren't lost.
            await trace_storage.record_turn(
                session,
                conversation_id=conversation_id,
                events=trace_sink,
                raw_msg_ids=msg_id_sink,
            )
        await session.commit()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get(
    "/api/agents/{agent_id}/conversations",
    response_model=list[ConversationResponse],
)
async def list_conversations(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agent = await chat_service.get_agent_with_tools(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    return await chat_service.list_conversations(db, agent_id)


@router.post(
    "/api/agents/{agent_id}/conversations",
    response_model=ConversationResponse,
    status_code=201,
)
async def create_conversation(
    agent_id: uuid.UUID,
    data: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    agent = await chat_service.get_agent_with_tools(db, agent_id, user.id)
    if not agent:
        raise agent_not_found()
    return await chat_service.create_conversation(db, agent_id, data.title)


@router.patch(
    "/api/conversations/{conversation_id}",
    response_model=ConversationResponse,
)
async def update_conversation(
    conversation_id: uuid.UUID,
    data: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
):
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()
    return await chat_service.update_conversation(db, conv, data)


@router.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()
    await chat_service.delete_conversation(db, conv)


@router.get(
    "/api/conversations/{conversation_id}/traces",
    response_model=list[TurnTraceResponse],
)
async def list_traces(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """W5 — return all turn traces (SSE event arrays) for a conversation.

    Each row is one assistant turn captured by ``stream_agent_response``,
    ordered by ``created_at`` ascending. Used by W6 shared page chip
    rendering and (later) W3-out for resume.
    """
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()
    return await trace_storage.get_traces_for_conversation(db, conversation_id)


@router.get(
    "/api/conversations/{conversation_id}/messages",
    response_model=MessagesEnvelope,
)
async def list_messages(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Return all branches as a tree-shaped envelope.

    M-CHAT1b: previously returned a flat ``list[MessageResponse]``. The new
    envelope wraps the same list with ``active_tip_message_id`` /
    ``active_checkpoint_id`` so the frontend can build assistant-ui's
    ``messageRepository``. Threads with no branching still return a single
    linear chain — only ``messages[].siblings`` carries the new info.
    """

    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()

    # P0-D: build tree once and reuse — pass into list_messages_from_checkpointer
    # so it doesn't repeat the _collect_checkpoints + tree build.
    from app.agent_runtime.checkpointer import get_checkpointer

    tree = None
    try:
        checkpointer = get_checkpointer()
        tree = await thread_branch_service.build_message_tree(
            checkpointer,
            str(conversation_id),
            active_checkpoint_id=conv.active_branch_checkpoint_id,
        )
    except RuntimeError:
        # checkpointer not initialized (e.g. unit tests that never spin up
        # PostgreSQL). list_messages_from_checkpointer will rebuild on its own
        # path or return [] — either way, we degrade to a flat envelope.
        tree = None

    messages = await chat_service.list_messages_from_checkpointer(
        db, conv, user_id=user.id, tree=tree
    )

    # W7-4 — conversation 누적 cost. agent.model의 단가로 메시지마다 계산된
    # ``estimated_cost``를 합산. ``token_usages`` 테이블은 사실상 unused
    # (Daily Spend가 별도 path로 누적)이라 신뢰할 수 없음.
    total_cost = sum(
        (m.usage.estimated_cost or 0.0)
        for m in messages
        if m.usage and m.usage.estimated_cost is not None
    )

    if tree is None:
        return MessagesEnvelope(messages=messages, total_estimated_cost=total_cost)

    active_tip: uuid.UUID | None = None
    if tree.active_tip_message_id:
        from app.agent_runtime.message_utils import parse_msg_id

        # parse_msg_id is deterministic over the raw langchain id so the
        # uuid it produces here matches the one already on every
        # MessageResponse. The idx arg is only used as a synthetic-id
        # fallback (the tip always carries a real id), so 0 is fine.
        active_tip = parse_msg_id(
            tree.active_tip_message_id, conversation_id, 0
        )
    return MessagesEnvelope(
        messages=messages,
        active_tip_message_id=active_tip,
        active_checkpoint_id=conv.active_branch_checkpoint_id
        or tree.active_checkpoint_id,
        total_estimated_cost=total_cost,
    )




# ---------------------------------------------------------------------------
# W3-out M3 — GET stream resume
# ---------------------------------------------------------------------------


def _is_pending_interrupt(events: list[dict[str, Any]] | None) -> bool:
    """Detect HiTL interrupt waiting for ``/messages/resume``.

    DB row events 의 마지막이 ``interrupt`` 이고 ``message_end`` 가 한 번도
    오지 않았다면 graph 가 일시정지 상태. 이 경우 stream resume(GET)이 아니라
    HiTL graph resume(POST `/messages/resume`)으로 응답해야 하므로 409 로
    차단한다 (plan 시나리오 D + 위험 항목).
    """
    if not events:
        return False
    has_message_end = any(
        evt.get("event") == event_names.MESSAGE_END for evt in events
    )
    if has_message_end:
        return False
    return events[-1].get("event") == event_names.INTERRUPT


def _normalize_event_id(raw: object) -> str | None:
    """빈 문자열 / non-str 을 None 으로 정규화. SSE ``id:`` 라인 생략 신호."""
    return raw if isinstance(raw, str) and raw else None


async def _broker_resume_generator(
    broker: EventBroker, after_id: str | None
) -> AsyncGenerator[str, None]:
    """Subscribe to a live broker → SSE chunks.

    ``broker.subscribe`` 가 buffer replay (after_id 이후) → live tail 순서로
    이벤트를 yield. broker close 시 자연스럽게 종료된다.
    """
    async for evt in broker.subscribe(after_id=after_id):
        yield format_sse(
            evt["event"], evt["data"], event_id=_normalize_event_id(evt.get("id"))
        )


async def _replay_resume_generator(
    record: MessageEvent,
    after_id: str | None,
    *,
    mark_stale: bool,
) -> AsyncGenerator[str, None]:
    """Replay events from DB row, optionally marking the stream as stale.

    broker 가 죽고 row.status='streaming' (정상 종료 신호 message_end 미수신)
    이면 마지막에 ``event: stale`` 을 발행해 client 에 broker 손실을 알린다.
    client 는 이 이벤트를 받으면 turn 이 backend 에서 끊겼음을 인지하고
    자동 재시도를 멈춘다.

    Corrupt row (``event`` 필드 누락) 항목은 SSE 표준상 ``"message"`` 채널로
    오해되어 client 가 정체불명 데이터를 받게 되므로 skip + warning.

    stale payload 의 ``last_event_id`` 는 row.last_event_id → 방금 yield 한
    마지막 evt id → 둘 다 없으면 ``reason='broker_lost_no_id'`` 분기 (NPE 회피).
    """
    last_emitted_id: str | None = None
    for evt in slice_events_after(record.events or [], after_id):
        evt_name = evt.get("event")
        if not isinstance(evt_name, str) or not evt_name:
            logger.warning(
                "stream_resume skip corrupt evt msg_id=%s evt_id=%s",
                record.assistant_msg_id, evt.get("id"),
            )
            continue
        emitted_id = _normalize_event_id(evt.get("id"))
        yield format_sse(evt_name, evt.get("data") or {}, event_id=emitted_id)
        if emitted_id:
            last_emitted_id = emitted_id

    if mark_stale:
        stale_id = record.last_event_id or last_emitted_id
        yield format_sse(
            event_names.STALE,
            {
                "reason": "broker_lost" if stale_id else "broker_lost_no_id",
                "last_event_id": stale_id,
            },
        )


def _log_resume_reject(
    reason: str,
    conversation_id: uuid.UUID,
    run_id_str: str,
    **extra: Any,
) -> None:
    """가드 분기에서 외부 응답은 단일화 (RESUME_NOT_FOUND) 하되 서버 로그로
    reason 구분이 가능하게. simplify #2 — 6개 호출 사이트 중복 제거.
    """
    suffix = (
        " " + " ".join(f"{k}={v}" for k, v in extra.items()) if extra else ""
    )
    logger.info(
        "stream_resume reject reason=%s conv=%s run_id=%s%s",
        reason, conversation_id, run_id_str, suffix,
    )


@router.get("/api/conversations/{conversation_id}/stream")
async def stream_resume(
    conversation_id: uuid.UUID,
    run_id: uuid.UUID,
    last_event_id: str | None = Query(None),
    last_event_id_header: str | None = Header(None, alias="Last-Event-ID"),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """Resume an in-flight or recently-completed assistant SSE stream.

    분기 (plan M3):
    1. broker live → ``EventBroker.subscribe`` 로 누락분 replay + 라이브 tail.
       header ``X-Resume-Mode: live``.
    2. broker miss + DB row → events 슬라이스 emit (after_id 이후).
       header ``X-Resume-Mode: replay``. ``status='streaming'`` 인 row 는
       ``event: stale`` 을 마지막에 발행 (broker 가 끊긴 채로 message_end 가
       오지 않은 케이스).
    3. row 의 마지막 이벤트가 HiTL interrupt → ``409
       RESUME_INTERRUPT_PENDING``. client 는 ``/messages/resume`` 으로 다시
       와야 한다.
    4. 그 외 (conv 없음, ownership 실패, DB row 없음, broker 가 다른 conv
       소속, broker conv_id 가 None) → ``404 RESUME_NOT_FOUND`` 단일 응답.
       각 분기는 ``logger.info`` 로만 구분 (rules/security.md —
       enumeration oracle 방지: 외부 응답을 통일하고 내부 로그로 분기).

    ``last_event_id`` 는 query 우선, ``Last-Event-ID`` 헤더 fallback. SSE
    표준 헤더를 지원해서 EventSource 같은 brower native 도 호환.
    """
    after_id = last_event_id or last_event_id_header
    run_id_str = str(run_id)

    # 단일 join (conversations ⨝ agents on user_id) — 이전엔 get_conversation +
    # get_agent_for_user 두 round-trip 으로 conv 존재성 + ownership 을 분리
    # 검증했지만, 외부 응답이 어차피 단일 RESUME_NOT_FOUND 로 통일되므로 (rules
    # /security.md enumeration oracle) 두 분기를 분간할 가치가 없다. round-trip
    # 절감 + reason 명도 단일화 (디버깅 시 user=... 로 주체 식별 가능).
    conv = await chat_service.get_owned_conversation(
        db, conversation_id, user.id
    )
    if conv is None:
        _log_resume_reject(
            "conv_unowned_or_missing", conversation_id, run_id_str, user=user.id
        )
        raise resume_not_found()

    broker = broker_registry.get(run_id_str)
    if broker is not None and not broker.is_closed:
        # broker.conversation_id 가 URL conv 와 다르면 (None 포함) cross-conv
        # 누설 가능 — fail-closed. _prepare_stream_context 가 항상 conv_id 를
        # 전달하므로 None 은 비정상 신호.
        if broker.conversation_id != str(conversation_id):
            _log_resume_reject(
                "broker_conv_mismatch", conversation_id, run_id_str,
                broker_conv=broker.conversation_id,
            )
            raise resume_not_found()
        logger.info(
            "stream_resume mode=live conv=%s run_id=%s after_id=%s",
            conversation_id, run_id_str, after_id,
        )
        return _sse_response(
            _broker_resume_generator(broker, after_id),
            extra_headers={
                "X-Run-Id": run_id_str,
                "X-Resume-Mode": "live",
            },
        )

    record = await trace_storage.get_trace_by_msg_id(db, run_id_str)
    if record is None:
        _log_resume_reject("row_missing", conversation_id, run_id_str)
        raise resume_not_found()
    if record.conversation_id != conversation_id:
        _log_resume_reject(
            "row_conv_mismatch", conversation_id, run_id_str,
            row_conv=record.conversation_id,
        )
        raise resume_not_found()
    if _is_pending_interrupt(record.events):
        _log_resume_reject("interrupt_pending", conversation_id, run_id_str)
        raise resume_interrupt_pending()

    is_stale = record.status == "streaming"
    if is_stale:
        logger.warning(
            "stream_resume stale conv=%s run_id=%s last_event_id=%s "
            "(broker lost while turn streaming)",
            conversation_id, run_id_str, record.last_event_id,
        )
    logger.info(
        "stream_resume mode=replay conv=%s run_id=%s after_id=%s status=%s",
        conversation_id, run_id_str, after_id, record.status,
    )
    return _sse_response(
        _replay_resume_generator(record, after_id, mark_stale=is_stale),
        extra_headers={
            "X-Run-Id": run_id_str,
            "X-Resume-Mode": "replay",
        },
    )


# ---------------------------------------------------------------------------
# Streaming: send + resume
# ---------------------------------------------------------------------------


@router.post("/api/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID,
    data: MessageCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    cfg = await _resolve_agent_context(db, conversation_id, user)
    await chat_service.maybe_set_auto_title(db, conversation_id, data.content)
    # 메시지 송신 시점에 conv.updated_at 갱신 → list_messages refetch에서 정확한 base.
    # generate() 안에서 호출하면 SSE 응답 후 db session이 close되어 실패 가능.
    await chat_service.touch_conversation(db, conversation_id)

    # P1-7 — link uploaded attachments to this conversation. We don't yet
    # know the LangGraph user-message id, so we stamp ``conversation_id``
    # only; ``message_id`` is filled in once the message is committed in a
    # follow-up (frontend currently displays attachments by upload id).
    if data.attachments:
        await chat_service.link_attachments_to_conversation(
            db,
            conversation_id=conversation_id,
            user_id=user.id,
            attachment_ids=[a.id for a in data.attachments],
        )

    ctx = _prepare_stream_context(conversation_id)
    return _sse_handler(
        lambda: execute_agent_stream(
            cfg,
            [{"role": "user", "content": data.content}],
            **ctx.as_stream_kwargs(),
        ),
        log_msg=f"Agent stream failed for conversation {conversation_id}",
        user_msg="에이전트 실행 중 오류가 발생했습니다.",
        run_id=ctx.run_id,
        on_complete=ctx.finalize_callback(conversation_id),
    )


@router.post("/api/conversations/{conversation_id}/messages/resume")
async def resume_message(
    conversation_id: uuid.UUID,
    data: ResumeRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """HiTL interrupt 재개 — Command(resume=response)로 그래프 실행 재개."""
    cfg = await _resolve_agent_context(db, conversation_id, user)
    await chat_service.touch_conversation(db, conversation_id)

    ctx = _prepare_stream_context(conversation_id)
    return _sse_handler(
        lambda: resume_agent_stream(
            cfg, data.response, **ctx.as_stream_kwargs()
        ),
        log_msg=f"Agent resume failed for conversation {conversation_id}",
        user_msg="에이전트 재개 중 오류가 발생했습니다.",
        run_id=ctx.run_id,
        on_complete=ctx.finalize_callback(conversation_id),
    )


# ---------------------------------------------------------------------------
# Branch operations: edit / regenerate / switch
# ---------------------------------------------------------------------------


async def _resolve_branch_checkpoint(
    conversation_id: uuid.UUID,
    target_message_id: uuid.UUID,
    *,
    checkpoints: list | None = None,
) -> str | None:
    """Translate ``target_message_id`` (UUID) → LangGraph checkpoint to fork from.

    The frontend hands back the same UUID we exposed via ``MessageResponse.id``.
    That UUID was either the raw langchain message id (when it parsed cleanly)
    or a deterministic uuid5 derived from the raw id. To rewind we need the
    raw id again — we recover it by walking checkpoints and matching the
    parsed UUID against ``parse_msg_id(raw, conversation_id, idx)``.

    P0-D: ``checkpoints`` can be passed in by callers that already collected
    them (avoids the duplicate ``_collect_checkpoints`` round-trip on
    edit/regenerate paths).
    """

    from app.agent_runtime.checkpointer import get_checkpointer
    from app.agent_runtime.message_utils import parse_msg_id
    from app.services.thread_branch_service import (
        _collect_checkpoints,  # noqa: PLC2701 — internal helper, controlled use
        rewind_to_checkpoint_before_message,
    )

    checkpointer = get_checkpointer()
    if checkpoints is None:
        checkpoints = await _collect_checkpoints(checkpointer, str(conversation_id))
    target_uuid_str = str(target_message_id)
    raw_id: str | None = None
    for ck in checkpoints:
        for idx, msg in enumerate(ck.messages):
            raw = getattr(msg, "id", None)
            if str(parse_msg_id(raw, conversation_id, idx)) == target_uuid_str:
                raw_id = str(raw or f"synthetic-{idx}")
                break
        if raw_id is not None:
            break
    if raw_id is None:
        return None
    return await rewind_to_checkpoint_before_message(
        checkpointer, str(conversation_id), raw_id
    )


@router.post("/api/conversations/{conversation_id}/messages/edit")
async def edit_message(
    conversation_id: uuid.UUID,
    data: EditMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Replace a previous user message and re-run.

    Implementation: rewind to the checkpoint *before* ``message_id`` and
    invoke the agent with ``new_content`` while passing that checkpoint_id as
    ``configurable.checkpoint_id`` — LangGraph forks a sibling subtree.
    """

    checkpoint_id = await _resolve_branch_checkpoint(conversation_id, data.message_id)
    cfg = await _resolve_agent_context(
        db, conversation_id, user, checkpoint_id=checkpoint_id
    )
    await chat_service.touch_conversation(db, conversation_id)
    # Edit creates a new leaf — drop any prior user-pinned branch so the
    # newest (just-edited) branch becomes the displayed one on next list.
    await chat_service.clear_active_branch_override(db, conversation_id)

    ctx = _prepare_stream_context(conversation_id)
    return _sse_handler(
        lambda: execute_agent_stream(
            cfg,
            [{"role": "user", "content": data.new_content}],
            **ctx.as_stream_kwargs(),
        ),
        log_msg=f"Agent edit failed for conversation {conversation_id}",
        user_msg="메시지 편집 중 오류가 발생했습니다.",
        run_id=ctx.run_id,
        on_complete=ctx.finalize_callback(conversation_id),
    )


@router.post("/api/conversations/{conversation_id}/messages/regenerate")
async def regenerate_message(
    conversation_id: uuid.UUID,
    data: RegenerateMessageRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Regenerate an assistant message in place by replaying its parent user turn."""

    from app.agent_runtime.checkpointer import get_checkpointer
    from app.agent_runtime.message_utils import parse_msg_id
    from app.services.thread_branch_service import _collect_checkpoints  # noqa: PLC2701

    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()

    checkpointer = get_checkpointer()
    checkpoints = await _collect_checkpoints(checkpointer, str(conversation_id))
    if not checkpoints:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail="conversation has no history yet.")

    # Pick the active branch tip (or the named assistant message) and find
    # the user message that precedes it.
    active = checkpoints[0]
    msgs = active.messages
    target_idx: int | None = None
    if data.message_id is None:
        # Walk back from the tip to the latest assistant message.
        for i in range(len(msgs) - 1, -1, -1):
            if getattr(msgs[i], "type", None) == "ai":
                target_idx = i
                break
    else:
        target_uuid_str = str(data.message_id)
        for i, m in enumerate(msgs):
            raw = getattr(m, "id", None)
            if str(parse_msg_id(raw, conversation_id, i)) == target_uuid_str:
                target_idx = i
                break

    if target_idx is None or target_idx == 0:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=422, detail="cannot regenerate — no parent user message."
        )

    # B5 fix — rewind to the checkpoint that contains the parent user turn
    # but NOT the assistant reply we're regenerating, then resume the graph
    # with NO new input. LangGraph re-runs from that state and produces a
    # sibling assistant message. Passing the user content as input (the old
    # behaviour) caused the user turn to be appended a second time, so the
    # frontend rendered "[user, user, ai_new]" instead of "[user, ai_new]".
    from app.services.thread_branch_service import rewind_to_checkpoint_before_message

    target_msg = msgs[target_idx]
    target_msg_raw = getattr(target_msg, "id", None) or f"synthetic-{target_idx}"
    checkpoint_id = await rewind_to_checkpoint_before_message(
        checkpointer, str(conversation_id), target_msg_raw
    )

    cfg = await _resolve_agent_context(
        db, conversation_id, user, checkpoint_id=checkpoint_id
    )
    await chat_service.touch_conversation(db, conversation_id)
    # Regenerate creates a new leaf — drop any prior user-pinned branch.
    await chat_service.clear_active_branch_override(db, conversation_id)

    # Empty messages_history → executor passes None to LangGraph,
    # resuming from the rewound checkpoint state without duplicating
    # the user message.
    ctx = _prepare_stream_context(conversation_id)
    return _sse_handler(
        lambda: execute_agent_stream(cfg, [], **ctx.as_stream_kwargs()),
        log_msg=f"Agent regenerate failed for conversation {conversation_id}",
        user_msg="메시지 재생성 중 오류가 발생했습니다.",
        run_id=ctx.run_id,
        on_complete=ctx.finalize_callback(conversation_id),
    )


@router.post(
    "/api/conversations/{conversation_id}/messages/switch-branch",
    status_code=204,
)
async def switch_branch(
    conversation_id: uuid.UUID,
    data: SwitchBranchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Record the user's branch choice on the conversation row."""

    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()
    from sqlalchemy import update as _update

    from app.models.conversation import Conversation as _Conv

    await db.execute(
        _update(_Conv)
        .where(_Conv.id == conversation_id)
        .values(active_branch_checkpoint_id=data.checkpoint_id)
    )
    await db.commit()


# ---------------------------------------------------------------------------
# File download
# ---------------------------------------------------------------------------


@router.get("/api/conversations/{conversation_id}/files/{file_path:path}")
async def get_conversation_file(
    conversation_id: uuid.UUID,
    file_path: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    conv = await chat_service.get_conversation(db, conversation_id)
    if not conv:
        raise conversation_not_found()

    base = Path(settings.conversation_output_dir) / str(conversation_id)
    target = (base / file_path).resolve()
    if not target.is_relative_to(base.resolve()) or not target.is_file():
        raise file_not_found()
    return FileResponse(target)
