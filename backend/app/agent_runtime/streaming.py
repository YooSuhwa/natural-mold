from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import orjson
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from app.agent_runtime.message_utils import content_to_text, extract_usage_breakdown

logger = logging.getLogger(__name__)


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
) -> AsyncGenerator[str, None]:
    """Stream agent SSE events.

    ``usage_sink`` (optional) is populated in-place when the stream finishes
    so callers (executor, hook framework) can record the captured token /
    cost numbers without re-parsing SSE. Keys: ``prompt_tokens``,
    ``completion_tokens``, ``estimated_cost``.

    ``trace_sink`` (optional, W5) is appended to with the dict form of each
    SSE event ``{"id", "event", "data"}`` so callers can persist the full
    trace at end-of-turn without re-parsing SSE strings.
    """
    msg_id = str(uuid.uuid4())

    # 시퀀스 카운터 — SSE id 필드를 ``{msg_id}-{seq}``로 발행해서 같은 stream
    # 내 dedup이 가능하게 한다. seq는 closure로 주입되어 emit 헬퍼 안에서만
    # mutate된다.
    seq = 0

    def emit(event: str, data: dict[str, Any]) -> str:
        nonlocal seq
        seq += 1
        event_id = f"{msg_id}-{seq}"
        if trace_sink is not None:
            trace_sink.append({"id": event_id, "event": event, "data": data})
        return format_sse(event, data, event_id=event_id)

    yield emit("message_start", {"id": msg_id, "role": "assistant"})

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
                                yield emit("content_delta", {"delta": _pending})
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
                                        yield emit("content_delta", {"delta": _buf})
                                        _buf = ""
                        else:
                            _pending += ch
                    # Flush remaining pending text from this LLM chunk
                    if _pending:
                        full_content += _pending
                        yield emit("content_delta", {"delta": _pending})

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
                        "tool_call_start",
                        {
                            "tool_name": tc_name,
                            "parameters": tc.get("args", {}),
                        },
                    )

            if msg.type == "tool":
                tool_name = msg.name if hasattr(msg, "name") else ""
                # Internal middleware tool result도 UI 노출 X (start와 대칭)
                if tool_name not in _INTERNAL_TOOL_NAMES:
                    yield emit(
                        "tool_call_result",
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
        yield emit("error", {"message": str(e)})

    # Flush any remaining buffer (incomplete JSON = not middleware output)
    if _buf:
        full_content += _buf

    # HiTL: 그래프 상태에서 interrupt 감지 후 클라이언트에 emit
    try:
        state = await agent.aget_state(config)
        if state.tasks:
            for task in state.tasks:
                if task.interrupts:
                    for intr in task.interrupts:
                        yield emit(
                            "interrupt",
                            {
                                "interrupt_id": str(getattr(intr, "ns", "")),
                                "value": intr.value
                                if isinstance(intr.value, dict)
                                else {"message": str(intr.value)},
                            },
                        )
    except Exception:
        logger.warning("aget_state failed (interrupt check)", exc_info=True)
        if was_interrupted:
            yield emit(
                "interrupt",
                {
                    "interrupt_id": "",
                    "value": {"message": "Interrupt detected but state unavailable"},
                },
            )

    # Calculate estimated cost from model pricing if available
    if usage_data and (cost_per_input_token or cost_per_output_token):
        prompt = usage_data.get("prompt_tokens", 0)
        completion = usage_data.get("completion_tokens", 0)
        cost = (prompt * (cost_per_input_token or 0)) + (completion * (cost_per_output_token or 0))
        usage_data["estimated_cost"] = round(cost, 8)  # type: ignore[assignment]  # SSE payload는 float 허용

    # Surface captured usage to the caller (executor → hook framework).
    if usage_sink is not None and usage_data:
        usage_sink.update(usage_data)

    yield emit("message_end", {"usage": usage_data, "content": full_content})
