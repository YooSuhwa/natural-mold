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
MEMORY_PROPOSED: Final = "memory_proposed"
MEMORY_SAVED: Final = "memory_saved"
MEMORY_REJECTED: Final = "memory_rejected"
MEMORY_DELETED: Final = "memory_deleted"

# Resume-only — W3-out M3 GET endpoint 가 broker 가 죽은 채로 streaming
# row 만 남은 경우 발행. client 는 이 이벤트를 받으면 자동 재시도를 멈춘다.
STALE: Final = "stale"
