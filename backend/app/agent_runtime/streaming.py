from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

import orjson
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from app.agent_runtime import event_names
from app.agent_runtime.event_broker import BrokeredEvent, EventBroker
from app.agent_runtime.message_utils import content_to_text, extract_usage_breakdown
from app.marketplace.redaction import redact_keys

logger = logging.getLogger(__name__)


# W3-out M2 — partial flush thresholds. 32 events 또는 2초 도래 시
# persist_callback을 fire-and-forget으로 호출. plan 결정 #4 참조.
_FLUSH_BATCH_SIZE = 32
_FLUSH_INTERVAL_SECONDS = 2.0
# Backpressure cap on in-flight partial flushes. DB가 느려져도 task /
# connection 폭주 방지. 4 = async DB pool(보통 10~20)의 보수적 1/3로
# 다른 라우트의 DB 작업과 풀 공유 여지 확보. 한도 도달 시 새 chunk는
# flush_buffer 에 그대로 보관되고 다음 임계치에서 재검사 → in-flight
# 가 비면 flush 재개. 최악의 경우 finally 의 final flush 가 잔여 처리.
_MAX_INFLIGHT_FLUSHES = 4
# retry_buffer 메모리 한도 (events 수). 평균 200B × 5000 = ~1MB.
# DB 영속 장애로 한 turn 동안 모든 partial flush 가 실패하더라도 OOM
# 보호. 초과 시 oldest chunk부터 drop + log (정상 turn 길이 0~수백
# events 가정 시 한도 도달은 이상 신호).
_MAX_RETRY_BUFFER_EVENTS = 5000


PersistCallback = Callable[[list[dict[str, Any]]], Awaitable[None]]


@dataclass(frozen=True)
class StreamErrorRecord:
    """Typed sink record for stream-visible runtime failures."""

    error: Exception
    message: str


def format_sse(event: str, data: dict[str, Any], *, event_id: str | None = None) -> str:
    # orjson은 stdlib json 대비 3~5x 빠르고 ensure_ascii 비활성이 기본 (UTF-8
    # bytes 그대로). SSE는 매 토큰 chunk마다 호출되는 hot path.
    #
    # ``event_id`` (선택): SSE 표준 ``id:`` 필드. 클라이언트가 동일 stream 재시도
    # 시 중복 이벤트를 dedup하거나 stale 이벤트를 폐기할 수 있게 한다.
    payload = orjson.dumps(data).decode()
    if event_id:
        return f"event: {event}\nid: {event_id}\ndata: {payload}\n\n"
    return f"event: {event}\ndata: {payload}\n\n"


def _message_to_trace_input(message: Any) -> dict[str, Any] | Any:
    if isinstance(message, dict):
        return {key: _trace_input_payload(value) for key, value in message.items()}

    msg_type = getattr(message, "type", None)
    content = getattr(message, "content", None)
    if isinstance(msg_type, str) and content is not None:
        payload: dict[str, Any] = {
            "role": "user" if msg_type == "human" else msg_type,
            "content": content_to_text(content),
        }
        name = getattr(message, "name", None)
        if isinstance(name, str) and name:
            payload["name"] = name
        msg_id = getattr(message, "id", None)
        if isinstance(msg_id, str) and msg_id:
            payload["id"] = msg_id
        return payload

    return _trace_input_payload(message)


def _trace_input_payload(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Command):
        return {"resume": _trace_input_payload(value.resume)}
    if isinstance(value, dict):
        return {key: _trace_input_payload(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_message_to_trace_input(item) for item in value]
    return str(value)


def _debug_input_for_message_start(actual_input: Any) -> Any:
    if actual_input is None:
        return None
    return redact_keys(_trace_input_payload(actual_input))


# Middleware-internal schema names (LLMToolSelectorMiddleware 등) — UI 노출 X.
_INTERNAL_TOOL_NAMES: frozenset[str] = frozenset({"ToolSelectionResponse"})


def _is_tool_selector_json(text: str) -> bool:
    """Check if text is LLMToolSelectorMiddleware output like {"tools":[...]}.

    ADR-004: PatchToolCallsMiddleware는 before_agent() 훅만 구현.
    스트림 이벤트를 필터링하지 않으므로 이 필터가 여전히 필요.

    Strict: only matches when "tools" is the sole key to avoid
    false positives on legitimate agent JSON output.
    """
    try:
        parsed = json.loads(text)
        return (
            isinstance(parsed, dict)
            and set(parsed.keys()) == {"tools"}
            and isinstance(parsed["tools"], list)
        )
    except (json.JSONDecodeError, ValueError):
        return False


def _interrupt_to_standard_chunk(
    intr_id: str, intr_value: dict[str, Any] | None
) -> dict[str, Any] | None:
    """LangGraph interrupt value를 표준 wire chunk로 정규화.

    - 표준 미들웨어 ``HITLRequest`` (action_requests/review_configs): 그대로 사용.
    - 자체 ``ask_user.py`` native interrupt (``{"type":"ask_user","question",
      "options"}``): 표준 ``respond`` 단일 액션으로 어댑트. 표준 미들웨어가
      ask_user 도구를 wrap하면 자연스럽게 도달 X — fallback 안전망.
    - 그 외 dict: skip (None 반환).
    """
    if intr_value is None:
        return None
    if "action_requests" in intr_value and "review_configs" in intr_value:
        return {
            "interrupt_id": intr_id,
            "action_requests": intr_value["action_requests"],
            "review_configs": intr_value["review_configs"],
        }
    if intr_value.get("type") == "ask_user":
        args = {key: value for key, value in intr_value.items() if key != "type"}
        if "mode" not in args:
            args = {
                "question": args.get("question") or "",
                "options": args.get("options") or [],
            }
        return {
            "interrupt_id": intr_id,
            "action_requests": [
                {
                    "id": intr_id or "ask_user",
                    "name": "ask_user",
                    "args": args,
                    "type": "tool_call",
                }
            ],
            "review_configs": [
                {
                    "action_name": "ask_user",
                    "allowed_decisions": ["respond"],
                }
            ],
        }
    return None


async def stream_agent_response(
    agent: Any,
    input_: list[Any] | Command | dict[str, Any] | None,
    config: dict[str, Any],
    *,
    cost_per_input_token: float | None = None,
    cost_per_output_token: float | None = None,
    usage_sink: dict[str, Any] | None = None,
    trace_sink: list[dict[str, Any]] | None = None,
    msg_id_sink: list[str] | None = None,
    error_sink: list[StreamErrorRecord] | None = None,
    broker: EventBroker | None = None,
    persist_callback: PersistCallback | None = None,
    run_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """Stream agent SSE events.

    ``usage_sink`` (optional) is populated in-place when the stream finishes
    so callers (executor, hook framework) can record the captured token /
    cost numbers without re-parsing SSE. Keys: ``prompt_tokens``,
    ``completion_tokens``, ``estimated_cost``.

    ``trace_sink`` (optional, W5) is appended to with the dict form of each
    SSE event ``{"id", "event", "data"}`` so callers can persist the full
    trace at end-of-turn without re-parsing SSE strings.

    ``broker`` (optional, W3-out M2): 라이브 stream의 dual-write 채널. 모든
    emit이 ``broker.publish_nowait`` 로 전파되어 끊긴 클라이언트가 GET resume
    으로 attach하면 즉시 라이브 토큰을 이어 받는다. finally에서 ``close()``.

    ``persist_callback`` (optional, W3-out M2): partial flush 콜백. 32 events
    또는 2초 도래 시 ``asyncio.create_task`` 로 fire-and-forget 호출 (emit의
    latency 0). caller(router)는 fresh DB session으로 ``append_events`` 를
    호출하는 콜백을 바인딩한다.

    ``run_id`` (optional, W3-out M2): assistant_msg_id로 사용할 외부 주입 UUID
    문자열. router가 broker key + X-Run-Id 헤더와 일관되게 맞추기 위해 미리
    생성. None이면 기존처럼 자체 생성 (legacy / 단위 테스트 호환).
    """
    msg_id = run_id or str(uuid.uuid4())

    # 시퀀스 카운터 — SSE id 필드를 ``{msg_id}-{seq}``로 발행해서 같은 stream
    # 내 dedup이 가능하게 한다. seq는 closure로 주입되어 emit 헬퍼 안에서만
    # mutate된다.
    seq = 0
    flush_buffer: list[dict[str, Any]] = []
    last_flush_at = time.monotonic()
    background_persist_tasks: set[asyncio.Task[None]] = set()
    # Failed-chunk retry buffer — partial flush가 실패하면 final flush에서
    # 한 번 더 시도. DB 일시 장애로 chunk가 silently 사라지는 것을 막는
    # safety net. final flush도 실패하면 그때만 영구 손실 (log).
    retry_buffer: list[dict[str, Any]] = []

    async def _safe_persist(chunk: list[dict[str, Any]]) -> None:
        """Background task wrapper — swallow exceptions so a DB hiccup
        doesn't kill the live stream. 실패한 chunk는 retry_buffer에 보관
        해서 finally의 final flush에서 한 번 더 시도한다.

        retry_buffer 가 한도(``_MAX_RETRY_BUFFER_EVENTS``) 를 초과하면
        oldest 부터 drop + log (event 한 개 수준의 손실은 stream 유지
        보다 낮은 우선순위)."""
        if persist_callback is None:
            return
        try:
            await persist_callback(chunk)
        except Exception:
            logger.exception(
                "partial flush persist_callback failed (run_id=%s) — chunk queued for final retry",
                msg_id,
            )
            retry_buffer.extend(chunk)
            if len(retry_buffer) > _MAX_RETRY_BUFFER_EVENTS:
                overflow = len(retry_buffer) - _MAX_RETRY_BUFFER_EVENTS
                del retry_buffer[:overflow]
                logger.warning(
                    "retry_buffer overflow (run_id=%s) — dropped %d oldest "
                    "events to stay under cap=%d",
                    msg_id,
                    overflow,
                    _MAX_RETRY_BUFFER_EVENTS,
                )

    def emit(event: str, data: dict[str, Any]) -> str:
        nonlocal seq, last_flush_at
        seq += 1
        event_id = f"{msg_id}-{seq}"
        # 단일 dict 인스턴스를 trace_sink/broker/flush_buffer 가 공유. 구조가
        # ``BrokeredEvent`` TypedDict(id/event/data 3 키)와 동일해 broker 측
        # 에서 추가 변환 불필요. emit 이후 누구도 mutate하지 않으므로 공유
        # 안전 (리뷰: dict 1개만 allocate해서 메모리 절반). pyright invariant
        # 한계로 BrokeredEvent → dict[str, Any] cast 명시.
        evt_dict: dict[str, Any] = {"id": event_id, "event": event, "data": data}
        if trace_sink is not None:
            trace_sink.append(evt_dict)
        if broker is not None:
            broker.publish_nowait(cast(BrokeredEvent, evt_dict))
        if persist_callback is not None:
            flush_buffer.append(evt_dict)
            now = time.monotonic()
            # Backpressure: in-flight task 한도 초과 시 새 chunk를 flush 하지
            # 않고 buffer에 그대로 둔다. 다음 emit에서 다시 임계치 검사 →
            # in-flight가 비면 flush 재개. 최악의 경우 finally의 final flush가
            # 모든 잔여를 한꺼번에 처리.
            should_flush = (
                len(flush_buffer) >= _FLUSH_BATCH_SIZE
                or (now - last_flush_at) >= _FLUSH_INTERVAL_SECONDS
            ) and len(background_persist_tasks) < _MAX_INFLIGHT_FLUSHES
            if should_flush:
                chunk = flush_buffer.copy()
                flush_buffer.clear()
                last_flush_at = now
                task = asyncio.create_task(_safe_persist(chunk))
                background_persist_tasks.add(task)
                task.add_done_callback(background_persist_tasks.discard)
        return format_sse(event, data, event_id=event_id)

    # None → LangGraph time-travel resume (no new input, just re-run from
    #   the configured checkpoint state). Used by regenerate to produce a
    #   sibling assistant turn from the same user message without duplicating
    #   that user message into the thread history.
    # Command(resume=...) → 직접 전달
    # dict → 그대로 (Builder v3 초기 state inject용)
    # list → {"messages": ...} 래핑
    actual_input: Any
    if input_ is None:
        actual_input = None
    elif isinstance(input_, (Command, dict)):
        actual_input = input_
    else:
        actual_input = {"messages": input_}

    full_content = ""
    was_interrupted = False
    stream_failed = False
    usage_data: dict[str, int] = {}
    # AIMessageChunk가 같은 tool_call을 partial state로 반복 emit하므로 dedupe.
    emitted_tool_call_keys: set[tuple[str, str]] = set()
    # ADR-004: PatchToolCallsMiddleware가 스트림 필터링을 하지 않으므로
    # 문자 단위 버퍼링으로 미들웨어 JSON 감지/제거.
    # yield는 LLM 청크 단위로 배칭하여 SSE 이벤트 수를 줄임.
    _buf = ""
    _brace_depth = 0

    # W6 정확도 — 이 turn에 노출된 AI 메시지의 raw langchain id를 수집.
    # streaming 동안 같은 메시지가 chunk 여러 개로 쪼개져 들어오므로 dedup.
    _seen_ai_msg_ids: set[str] = set()

    # W3-out M2 — broker close + final flush + background flush join이 무조건
    # 실행되도록 message_start emit 직후부터 message_end 도달까지 outer
    # try/finally 로 감싼다. 클라이언트 disconnect 시(generator aclose)에도
    # finally 가 동작해 broker.close 가 보장된다.
    start_data: dict[str, Any] = {"id": msg_id, "role": "assistant"}
    debug_input = _debug_input_for_message_start(actual_input)
    if debug_input is not None:
        start_data["input"] = debug_input
    yield emit(event_names.MESSAGE_START, start_data)
    try:
        try:
            async for chunk in agent.astream(
                actual_input,
                config=config,
                stream_mode="messages",
            ):
                msg, metadata = chunk
                # Builder v3 sub-LLM 호출은 화면 스트림에서 제외 (helpers.py에서 tag 부여)
                chunk_tags = (metadata or {}).get("tags") or []
                if "builder:internal" in chunk_tags:
                    continue
                # W6: AI 메시지의 raw id 수집 (caller가 sink 제공 시).
                if msg_id_sink is not None and msg.type in ("ai", "AIMessageChunk"):
                    raw_id = getattr(msg, "id", None)
                    if isinstance(raw_id, str) and raw_id and raw_id not in _seen_ai_msg_ids:
                        _seen_ai_msg_ids.add(raw_id)
                        msg_id_sink.append(raw_id)
                if hasattr(msg, "content") and msg.content and msg.type in ("ai", "AIMessageChunk"):
                    # Anthropic은 multi-block content (text + tool_use 등)를 list[dict]로
                    # 보내므로 text 블록만 평탄화. message_utils의 공유 헬퍼 사용.
                    delta = content_to_text(msg.content)
                    if delta:
                        _pending = ""
                        for ch in delta:
                            if ch == "{" and _brace_depth == 0:
                                # Flush pending text before entering JSON buffering
                                if _pending:
                                    full_content += _pending
                                    yield emit(event_names.CONTENT_DELTA, {"delta": _pending})
                                    _pending = ""
                                _brace_depth = 1
                                _buf = ch
                            elif _brace_depth > 0:
                                _buf += ch
                                if ch == "{":
                                    _brace_depth += 1
                                elif ch == "}":
                                    _brace_depth -= 1
                                    if _brace_depth == 0:
                                        # Outermost brace closed — check if middleware output
                                        if _is_tool_selector_json(_buf):
                                            _buf = ""
                                        else:
                                            full_content += _buf
                                            yield emit(event_names.CONTENT_DELTA, {"delta": _buf})
                                            _buf = ""
                            else:
                                _pending += ch
                        # Flush remaining pending text from this LLM chunk
                        if _pending:
                            full_content += _pending
                            yield emit(event_names.CONTENT_DELTA, {"delta": _pending})

                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tc_name = tc.get("name", "")
                        # 빈 이름(아직 partial state) / 미들웨어 internal schema는 UI 노출 X
                        if not tc_name or tc_name in _INTERNAL_TOOL_NAMES:
                            continue
                        tc_id = tc.get("id") or ""
                        # id가 비어 있으면 dedupe 키로 쓰지 않는다 — 같은 이름의 서로
                        # 다른 tool_call이 collision되어 silently 누락되는 것을 방지.
                        if tc_id:
                            key = (tc_name, tc_id)
                            if key in emitted_tool_call_keys:
                                continue
                            emitted_tool_call_keys.add(key)
                        yield emit(
                            event_names.TOOL_CALL_START,
                            {
                                "tool_name": tc_name,
                                # ADR-017 §13.2 — heuristic key-pattern
                                # redaction (password / api_key / secret /
                                # token / access_key / refresh_token) so
                                # SSE consumers don't see secret-shaped
                                # values from any tool that accepts an
                                # auth-style argument. Skill tool already
                                # redacts its own results at the executor
                                # layer; this protects MCP/regular tools.
                                "parameters": redact_keys(tc.get("args", {})),
                            },
                        )

                if msg.type == "tool":
                    tool_name = msg.name if hasattr(msg, "name") else ""
                    # Internal middleware tool result도 UI 노출 X (start와 대칭)
                    if tool_name not in _INTERNAL_TOOL_NAMES:
                        yield emit(
                            event_names.TOOL_CALL_RESULT,
                            {
                                "tool_name": tool_name,
                                "result": msg.content
                                if isinstance(msg.content, str)
                                else str(msg.content),
                            },
                        )

                # LangChain ``usage_metadata``는 input/output 외에
                # ``input_token_details``로 cache_creation/cache_read를 분리해 전달
                # (Anthropic / OpenAI prompt caching). fetch 경로(``message_utils``)와
                # 동일한 평탄화 헬퍼를 재사용해 두 경로의 shape을 통일한다.
                extracted = extract_usage_breakdown(msg)
                if extracted is not None:
                    usage_data = {
                        "prompt_tokens": extracted.prompt_tokens,
                        "completion_tokens": extracted.completion_tokens,
                        "cache_creation_tokens": extracted.cache_creation_tokens,
                        "cache_read_tokens": extracted.cache_read_tokens,
                    }

        except GraphInterrupt:
            # interrupt()에 의한 정상적인 그래프 일시정지 — 에러가 아님
            # 아래 aget_state에서 interrupt 이벤트를 emit
            was_interrupted = True
        except Exception as e:
            stream_failed = True
            error_record = StreamErrorRecord(error=e, message=str(e))
            if error_sink is not None:
                error_sink.append(error_record)
            yield emit(event_names.ERROR, {"message": error_record.message})

        # Flush any remaining buffer (incomplete JSON = not middleware output)
        if _buf:
            full_content += _buf

        # HiTL: 그래프 상태에서 interrupt 감지 후 표준 wire로 emit.
        # 변환은 ``_interrupt_to_standard_chunk`` 단일 진입점이 담당
        # (자체 ask_user.py 어댑터 포함). fallback은 빈 표준 chunk로 발행.
        try:
            state = await agent.aget_state(config)
            if state.tasks:
                for task in state.tasks:
                    if task.interrupts:
                        for intr in task.interrupts:
                            intr_id = str(getattr(intr, "ns", ""))
                            intr_value = (
                                intr.value if isinstance(intr.value, dict) else None
                            )
                            chunk = _interrupt_to_standard_chunk(intr_id, intr_value)
                            if chunk is not None:
                                yield emit(event_names.INTERRUPT, chunk)
        except Exception:
            logger.warning("aget_state failed (interrupt check)", exc_info=True)
            if was_interrupted:
                # fallback: state 조회 실패라 정확한 action을 알 수 없다. 빈 표준
                # chunk를 emit — frontend는 빈 action_requests로 fallback UI 표시.
                yield emit(
                    event_names.INTERRUPT,
                    {
                        "interrupt_id": "",
                        "action_requests": [],
                        "review_configs": [],
                    },
                )

        # Calculate estimated cost from model pricing if available
        if usage_data and (cost_per_input_token or cost_per_output_token):
            prompt = usage_data.get("prompt_tokens", 0)
            completion = usage_data.get("completion_tokens", 0)
            cost = (prompt * (cost_per_input_token or 0)) + (
                completion * (cost_per_output_token or 0)
            )
            usage_data["estimated_cost"] = round(cost, 8)  # type: ignore[assignment]  # SSE payload는 float 허용

        # Surface captured usage to the caller (executor → hook framework).
        if usage_sink is not None and usage_data:
            usage_sink.update(usage_data)

        yield emit(
            event_names.MESSAGE_END,
            {
                "usage": usage_data,
                "content": full_content,
                "status": "failed" if stream_failed else "completed",
            },
        )
    finally:
        # W3-out M2 — final flush + background flush join + broker close.
        # 무조건 실행되어야 함 (정상 종료 / GraphInterrupt / Exception / 클라이언트
        # disconnect 시 generator aclose() 모두). 실패는 swallow + log.
        # 순서가 중요: (1) background tasks join → 진행 중 partial flush가
        # 끝나서 retry_buffer가 확정. (2) retry_buffer + flush_buffer 합쳐서
        # 한 번에 final flush → DB 일시 장애로 누락된 chunk 회복 마지막 기회.
        if background_persist_tasks:
            # 진행 중 fire-and-forget 태스크들 회수. 이 시점 이후로
            # retry_buffer에 새로 추가될 일은 없다.
            await asyncio.gather(*background_persist_tasks, return_exceptions=True)
        if persist_callback is not None and (retry_buffer or flush_buffer):
            # retry 우선 → 그 후 마지막으로 buffer에 남은 신규 chunk.
            # 두 번 호출하면 dedup-by-id가 idempotency를 보장.
            final_chunks: list[list[dict[str, Any]]] = []
            if retry_buffer:
                final_chunks.append(retry_buffer.copy())
                retry_buffer.clear()
            if flush_buffer:
                final_chunks.append(flush_buffer.copy())
                flush_buffer.clear()
            for chunk in final_chunks:
                try:
                    await persist_callback(chunk)
                except Exception:
                    logger.exception(
                        "final flush persist_callback failed "
                        "(run_id=%s) — %d events permanently lost",
                        msg_id,
                        len(chunk),
                    )
        if broker is not None:
            broker.close()
