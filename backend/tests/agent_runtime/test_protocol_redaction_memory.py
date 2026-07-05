"""memory 계열 custom 이벤트의 영속 redaction 계약 (W2-3 회상 이벤트 포함).

영속/공유 표면(redact_memory=True 기본값)에는 기억 내용이 남지 않아야 하고,
소유자 라이브 wire(redact_memory=False)는 내용을 그대로 전달한다.
"""

from __future__ import annotations

from app.agent_runtime.protocol_redaction import REDACTED_MEMORY_FIELD, redact_protocol_data


def _recalled_event() -> dict:
    return {
        "name": "moldy.memory_recalled",
        "payload": {
            "memories": [
                {"id": "m1", "scope": "user", "content": "한국어 선호"},
                {"id": "m2", "scope": "agent", "content": "표로 정리"},
            ]
        },
    }


def test_persistence_redacts_memory_recalled_content() -> None:
    redacted = redact_protocol_data("custom", _recalled_event())

    briefs = redacted["payload"]["memories"]
    assert [brief["content"] for brief in briefs] == [
        REDACTED_MEMORY_FIELD,
        REDACTED_MEMORY_FIELD,
    ]
    # id/scope는 유지 — 프론트가 리로드 시 메모리 API 재조회로 내용을 복원한다.
    assert [brief["id"] for brief in briefs] == ["m1", "m2"]
    assert [brief["scope"] for brief in briefs] == ["user", "agent"]


def test_live_wire_keeps_memory_recalled_content() -> None:
    passed = redact_protocol_data("custom", _recalled_event(), redact_memory=False)
    assert passed["payload"]["memories"][0]["content"] == "한국어 선호"


def test_memory_proposed_custom_event_still_redacted() -> None:
    event = {
        "name": "memory_proposed",
        "payload": {"id": "p1", "scope": "user", "content": "비밀 취향", "reason": "이유"},
    }
    redacted = redact_protocol_data("custom", event)
    assert redacted["payload"]["content"] == REDACTED_MEMORY_FIELD
    assert redacted["payload"]["reason"] == REDACTED_MEMORY_FIELD


def test_non_memory_custom_event_untouched() -> None:
    event = {"name": "moldy.subagent_names", "payload": {"names": {"agent_1": "리서처"}}}
    assert redact_protocol_data("custom", event) == event
