"""HiTL wire format 회귀 가드.

A. ``Decision`` Pydantic 검증
B. ``ResumeRequest`` (decisions 필수) Pydantic 검증
C. ``POST /api/conversations/{id}/messages/resume`` router → 표준 dict 페이로드
D. ``stream_agent_response`` ``GraphInterrupt`` catch 시 표준 chunk 단독 emit
   - 표준 미들웨어 HITLRequest shape: 그대로 emit
   - 자체 ``ask_user.py`` native interrupt: 표준 ``respond`` action으로 어댑트
   - ``aget_state`` 실패 fallback: 빈 표준 chunk

미들웨어 인스턴스화 가드는 ``test_hitl_middleware.py`` 별도 파일에 있다.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.agent_runtime.streaming import _interrupt_to_standard_chunk, stream_agent_response
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from app.schemas.conversation import Decision, ResumeRequest
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# A. Decision Pydantic 검증
# ---------------------------------------------------------------------------


class TestDecisionSchema:
    def test_approve_minimal_valid(self):
        d = Decision(type="approve")
        assert d.type == "approve"
        assert d.edited_action is None
        assert d.message is None

    def test_edit_requires_edited_action(self):
        with pytest.raises(ValidationError, match="edited_action"):
            Decision(type="edit")

    def test_edit_with_edited_action_valid(self):
        d = Decision(
            type="edit",
            edited_action={"name": "send_email", "args": {"to": "x@y"}},
        )
        assert d.edited_action is not None

    def test_respond_requires_message(self):
        with pytest.raises(ValidationError, match="message"):
            Decision(type="respond")

    def test_respond_with_message_valid(self):
        d = Decision(type="respond", message="hello")
        assert d.message == "hello"

    def test_reject_message_is_optional(self):
        # message 없이도 OK (미들웨어가 기본 메시지 생성).
        d = Decision(type="reject")
        assert d.type == "reject"
        d2 = Decision(type="reject", message="reason")
        assert d2.message == "reason"

    def test_type_literal_rejects_unknown(self):
        with pytest.raises(ValidationError):
            Decision(type="unknown")  # type: ignore[arg-type]

    def test_model_dump_excludes_none_for_typed_dict_compat(self):
        d = Decision(type="approve")
        dumped = d.model_dump(exclude_none=True)
        # LangChain HITLResponse TypedDict는 NotRequired — None 키 제외.
        assert dumped == {"type": "approve"}


# ---------------------------------------------------------------------------
# B. ResumeRequest 표준 단독
# ---------------------------------------------------------------------------


class TestResumeRequestSchema:
    def test_decisions_required(self):
        """``decisions`` 필드 누락 시 422."""
        with pytest.raises(ValidationError, match="decisions"):
            ResumeRequest()  # type: ignore[call-arg]

    def test_decisions_valid(self):
        req = ResumeRequest(decisions=[Decision(type="approve")])
        assert len(req.decisions) == 1


# ---------------------------------------------------------------------------
# C. Router payload — POST /messages/resume
# ---------------------------------------------------------------------------


async def _seed_user_agent_conv() -> uuid.UUID:
    async with TestSession() as db:
        user = User(id=TEST_USER_ID, email="test@test.com", name="Test")
        db.add(user)
        model = Model(provider="openai", model_name="gpt-4o", display_name="GPT-4o")
        db.add(model)
        await db.flush()
        agent = Agent(
            user_id=user.id,
            name="HiTL Agent",
            system_prompt="Hi",
            model_id=model.id,
        )
        db.add(agent)
        await db.flush()
        conv = Conversation(agent_id=agent.id, title="Resume Test")
        db.add(conv)
        await db.commit()
        return conv.id


def _capture_resume_payload() -> tuple[list[Any], Any]:
    captured: list[Any] = []

    async def fake_stream(*args, **kwargs):
        captured.append(args)
        captured.append(kwargs)
        yield 'event: message_end\ndata: {"content": "ok", "usage": {}}\n\n'

    return captured, fake_stream


class TestResumeRouterPayload:
    @pytest.mark.asyncio
    async def test_decisions_passed_through_as_command_resume_payload(
        self, client: AsyncClient
    ):
        conv_id = await _seed_user_agent_conv()
        captured, fake = _capture_resume_payload()

        with patch(
            "app.routers.conversations.resume_agent_stream", side_effect=fake
        ):
            resp = await client.post(
                f"/api/conversations/{conv_id}/messages/resume",
                json={
                    "decisions": [
                        {"type": "approve"},
                        {
                            "type": "edit",
                            "edited_action": {
                                "name": "send_email",
                                "args": {"to": "x@y", "subject": "hi"},
                            },
                        },
                    ]
                },
            )

        assert resp.status_code == 200
        payload = captured[0][1]
        assert payload == {
            "decisions": [
                {"type": "approve"},
                {
                    "type": "edit",
                    "edited_action": {
                        "name": "send_email",
                        "args": {"to": "x@y", "subject": "hi"},
                    },
                },
            ]
        }

    @pytest.mark.asyncio
    async def test_respond_decision_serialized(self, client: AsyncClient):
        conv_id = await _seed_user_agent_conv()
        captured, fake = _capture_resume_payload()

        with patch(
            "app.routers.conversations.resume_agent_stream", side_effect=fake
        ):
            resp = await client.post(
                f"/api/conversations/{conv_id}/messages/resume",
                json={"decisions": [{"type": "respond", "message": "yes"}]},
            )

        assert resp.status_code == 200
        payload = captured[0][1]
        assert payload == {
            "decisions": [{"type": "respond", "message": "yes"}]
        }

    @pytest.mark.asyncio
    async def test_empty_body_returns_422(self, client: AsyncClient):
        conv_id = await _seed_user_agent_conv()
        resp = await client.post(
            f"/api/conversations/{conv_id}/messages/resume",
            json={},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_legacy_response_field_rejected_422(self, client: AsyncClient):
        """legacy ``response`` 필드는 unknown — Pydantic 기본 ignore. ``decisions``가
        없으면 422.
        """
        conv_id = await _seed_user_agent_conv()
        resp = await client.post(
            f"/api/conversations/{conv_id}/messages/resume",
            json={"response": "legacy form"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# D. Streaming — 표준 chunk 단독
# ---------------------------------------------------------------------------


def _make_intr(ns: str, value: Any) -> MagicMock:
    intr = MagicMock()
    intr.ns = ns
    intr.value = value
    return intr


def _make_state_with_interrupts(interrupts: list[MagicMock]) -> MagicMock:
    state = MagicMock()
    task = MagicMock()
    task.interrupts = interrupts
    state.tasks = [task]
    return state


class _InterruptingAgent:
    def __init__(self, state: MagicMock | Exception):
        self._state = state

    async def astream(self, *args, **kwargs):
        from langgraph.errors import GraphInterrupt

        if False:
            yield  # pragma: no cover
        raise GraphInterrupt([])

    async def aget_state(self, config: Any):
        if isinstance(self._state, Exception):
            raise self._state
        return self._state


def _parse_interrupt_events(events: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in events:
        if "event: interrupt\n" not in raw:
            continue
        for line in raw.split("\n"):
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: ") :]))
    return out


class TestInterruptToStandardChunk:
    """``_interrupt_to_standard_chunk`` 단위 검증."""

    def test_standard_hitl_request_passthrough(self):
        intr_value = {
            "action_requests": [{"name": "send_email", "args": {"to": "x@y"}}],
            "review_configs": [{"action_name": "send_email", "allowed_decisions": ["approve"]}],
        }
        chunk = _interrupt_to_standard_chunk("ns-1", intr_value)
        assert chunk is not None
        assert chunk["interrupt_id"] == "ns-1"
        assert chunk["action_requests"] == intr_value["action_requests"]
        assert chunk["review_configs"] == intr_value["review_configs"]

    def test_ask_user_native_adapted_to_respond_action(self):
        """builder_v3 native interrupt (``{"type":"ask_user",...}``)
        → 표준 ``respond`` action으로 어댑트.

        메인 채팅 ask_user 도구는 Phase 4 (ADR-012 §Phase 4 옵션 B)에서
        retire되었으나, builder_v3 (``builder_v3/nodes/phase2_intent.py``)는
        자체 native interrupt 패턴을 유지하므로 streaming 어댑터가 본
        chunk shape을 처리해야 한다.
        """
        intr_value = {
            "type": "ask_user",
            "question": "어떤 옵션을 원하세요?",
            "options": ["A", "B"],
        }
        chunk = _interrupt_to_standard_chunk("ns-ask-1", intr_value)
        assert chunk is not None
        assert chunk["interrupt_id"] == "ns-ask-1"
        assert len(chunk["action_requests"]) == 1
        action = chunk["action_requests"][0]
        assert action["name"] == "ask_user"
        assert action["args"] == {"question": "어떤 옵션을 원하세요?", "options": ["A", "B"]}
        review = chunk["review_configs"][0]
        assert review["allowed_decisions"] == ["respond"]

    def test_unknown_shape_returns_none(self):
        """알 수 없는 dict shape은 skip (None)."""
        assert _interrupt_to_standard_chunk("ns", {"random": "stuff"}) is None
        assert _interrupt_to_standard_chunk("ns", None) is None


class TestStreamingStandardEmit:
    """``stream_agent_response``의 INTERRUPT chunk 표준 단독 emit."""

    @pytest.mark.asyncio
    async def test_standard_chunk_only_for_hitl_request(self):
        """표준 미들웨어 HITLRequest shape → 표준 chunk 1개만 emit."""
        intr_value = {
            "action_requests": [
                {
                    "name": "send_email",
                    "args": {"to": "x@y"},
                    "description": "Send confirmation",
                }
            ],
            "review_configs": [
                {
                    "action_name": "send_email",
                    "allowed_decisions": ["approve", "edit", "reject", "respond"],
                }
            ],
        }
        state = _make_state_with_interrupts([_make_intr("ns-42", intr_value)])
        agent = _InterruptingAgent(state)

        events = [e async for e in stream_agent_response(agent, [], {})]
        intrs = _parse_interrupt_events(events)

        assert len(intrs) == 1, "표준 단독 emit (legacy chunk 없음)"
        std = intrs[0]
        assert "action_requests" in std and "review_configs" in std
        assert "value" not in std, "legacy 'value' 키는 더 이상 emit되지 않음"
        assert std["interrupt_id"] == "ns-42"

    @pytest.mark.asyncio
    async def test_ask_user_native_emits_adapted_standard_chunk(self):
        """builder_v3 native interrupt 발행 시 streaming 어댑터가
        표준 wire chunk로 변환하여 단일 chunk emit (메인 채팅 ask_user
        도구는 Phase 4 retire — 본 어댑터는 builder_v3 영역 보존용).
        """
        intr_value = {"type": "ask_user", "question": "Choose?", "options": ["a", "b"]}
        state = _make_state_with_interrupts([_make_intr("ns-ask-1", intr_value)])
        agent = _InterruptingAgent(state)

        events = [e async for e in stream_agent_response(agent, [], {})]
        intrs = _parse_interrupt_events(events)

        assert len(intrs) == 1, "ask_user도 표준 chunk 단독"
        chunk = intrs[0]
        assert "action_requests" in chunk
        assert chunk["action_requests"][0]["name"] == "ask_user"
        assert chunk["interrupt_id"] == "ns-ask-1"

    @pytest.mark.asyncio
    async def test_unknown_shape_emits_no_chunk(self):
        """표준 shape도 ask_user도 아닌 dict는 chunk emit하지 않음 (skip)."""
        intr_value = {"random": "stuff"}
        state = _make_state_with_interrupts([_make_intr("ns-x", intr_value)])
        agent = _InterruptingAgent(state)

        events = [e async for e in stream_agent_response(agent, [], {})]
        intrs = _parse_interrupt_events(events)

        assert len(intrs) == 0

    @pytest.mark.asyncio
    async def test_fallback_empty_standard_chunk_when_aget_state_fails(self):
        """``aget_state`` 실패 + ``was_interrupted=True`` → 빈 표준 chunk."""
        agent = _InterruptingAgent(RuntimeError("aget_state boom"))

        events = [e async for e in stream_agent_response(agent, [], {})]
        intrs = _parse_interrupt_events(events)

        assert len(intrs) == 1
        chunk = intrs[0]
        assert chunk["interrupt_id"] == ""
        assert chunk["action_requests"] == []
        assert chunk["review_configs"] == []
        assert "value" not in chunk
