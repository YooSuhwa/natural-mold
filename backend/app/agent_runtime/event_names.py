"""Centralized SSE event name constants.

W3-out 트랙 도중 발견 — ``streaming.py`` 가 emit 하는 이벤트 이름과
``routers/conversations.py`` 의 검증 로직 (``_is_pending_interrupt`` /
``_replay_resume_generator``) 이 별개의 매직 스트링으로 정의되어 있어
한 쪽이 rename 되면 silent breakage 가 발생할 수 있다 (예: ``"interrupt"``
→ ``"hitl_pause"`` 변경 시 resume endpoint 의 409 차단이 silently no-op).

이 모듈은 단일 source of truth — emit 측과 검증 측이 모두 import 한다.
새 SSE 이벤트 추가 시 여기에 상수 등록.
"""

from __future__ import annotations

from typing import Final

# Producer side — ``streaming.py`` 의 emit 클로저가 발행하는 표준 이벤트.
MESSAGE_START: Final = "message_start"
CONTENT_DELTA: Final = "content_delta"
MESSAGE_END: Final = "message_end"
ERROR: Final = "error"
INTERRUPT: Final = "interrupt"
# wire format 은 ``tool_call_start`` / ``tool_call_result`` (frontend
# ``SSEEventType`` 와 일치). 초기 작성 시 ``tool_call`` / ``tool_result`` 로
# 잘못 둬 dead constant 였다 — 트랙 종료 시점 cross-file audit 에서 발견.
TOOL_CALL_START: Final = "tool_call_start"
TOOL_CALL_RESULT: Final = "tool_call_result"
FILE_EVENT: Final = "file_event"
# Auto-compaction side-channel (dev-plan-context-compaction-marker.md). Emitted as
# a ``custom`` protocol event (``name="moldy.compaction"``) carrying ``{state}`` —
# ``running`` while deepagents summarizes older messages, ``done`` once the
# ``_summarization_event`` is committed (with ``offload_path`` / ``cutoff_index``).
COMPACTION: Final = "moldy.compaction"
# Generative UI side-channel (chat-generative-ui-dev-plan §2.1). Emitted as a
# ``custom`` protocol event (``name="moldy.ui_data"``) carrying a typed
# ``{type, props}`` payload the frontend renders via an allowlist registry. Shares
# the ``custom`` channel with FILE_EVENT; consumers disambiguate by custom name.
UI_DATA_EVENT: Final = "moldy.ui_data"
# Subagent display-name side-channel (chat-subagent-streaming-visibility-plan G10).
# Emitted once at stream head as a ``custom`` protocol event
# (``name="moldy.subagent_names"``) carrying ``{names: {runtime_name: display_name}}``.
# The v3 path streams deepagents ``task`` tool calls whose ``subagent_type`` is the
# runtime name (``agent_<8hex>``); the frontend SDK uses that verbatim as the card
# title. Rather than rewrite the checkpoint-backed ``subagent_type`` (which drives
# execution + namespace binding), we ship the runtime_name→display_name map so the
# frontend can substitute a human-readable name at the display layer only. Stable
# event id dedupes on replay/reload (same contract as COMPACTION).
SUBAGENT_NAMES: Final = "moldy.subagent_names"
MEMORY_PROPOSED: Final = "memory_proposed"
MEMORY_SAVED: Final = "memory_saved"
MEMORY_REJECTED: Final = "memory_rejected"
MEMORY_DELETED: Final = "memory_deleted"

# Resume-only — W3-out M3 GET endpoint 가 broker 가 죽은 채로 streaming
# row 만 남은 경우 발행. client 는 이 이벤트를 받으면 자동 재시도를 멈춘다.
STALE: Final = "stale"
