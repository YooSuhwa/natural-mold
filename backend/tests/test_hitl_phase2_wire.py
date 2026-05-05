"""HiTL Phase 2 — Wire format dual-path 회귀 가드.

검증 대상 (모두 ``docs/exec-plans/active/hitl-phase2-contract.md`` 결정사항과
1:1 매칭):

A. ``Decision`` / ``ResumeRequest`` Pydantic 검증 (§2)
B. ``POST /api/conversations/{id}/messages/resume`` router의 표준/legacy 변환
   → ``resume_agent_stream(cfg, payload, ...)`` 페이로드 (§3, §2.4)
C. ``stream_agent_response`` ``GraphInterrupt`` catch 시 표준 + legacy 두
   chunk dual emit (§4.3, §4.4)
D. ``aget_state`` 실패 + ``was_interrupted=True`` fallback에서도 두 chunk
   emit, ``interrupt_id=""`` (§4.5)

Phase 1 가드(``test_hitl_middleware.py`` 5건)는 별도 파일로 보존.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.agent_runtime.streaming import stream_agent_response
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.model import Model
from app.models.user import User
from app.schemas.conversation import Decision, ResumeRequest
from tests.conftest import TEST_USER_ID, TestSession

# ---------------------------------------------------------------------------
# A. Pydantic 스키마 검증 — Decision / ResumeRequest (§2)
# ---------------------------------------------------------------------------


class TestDecisionSchema:
    """``Decision`` Pydantic 모델 (contract §2.1)."""

    def test_approve_minimal_valid(self):
        """``approve``는 추가 필드 없이 유효."""
        d = Decision(type="approve")
        assert d.type == "approve"
        assert d.edited_action is None
        assert d.message is None

    def test_edit_requires_edited_action(self):
        """``edit``는 ``edited_action`` 누락 시 ValidationError."""
        with pytest.raises(ValidationError, match="edited_action"):
            Decision(type="edit")

    def test_edit_with_edited_action_valid(self):
        d = Decision(
            type="edit",
            edited_action={"name": "send_email", "args": {"to": "x@y"}},
        )
        assert d.edited_action == {"name": "send_email", "args": {"to": "x@y"}}

    def test_respond_requires_message(self):
        """``respond``는 ``message`` 누락 시 ValidationError."""
        with pytest.raises(ValidationError, match="message"):
            Decision(type="respond")

    def test_respond_with_message_valid(self):
        d = Decision(type="respond", message="user reply")
        assert d.message == "user reply"

    def test_reject_message_is_optional(self):
        """``reject``는 ``message`` 없이도 유효 (없으면 미들웨어가 기본 메시지)."""
        d = Decision(type="reject")
        assert d.message is None
        d2 = Decision(type="reject", message="not allowed")
        assert d2.message == "not allowed"

    def test_type_literal_rejects_unknown(self):
        """4종 외 ``type``은 거부 (ADR-012 §3 표준 매칭 강제)."""
        with pytest.raises(ValidationError):
            Decision(type="unknown_action")  # type: ignore[arg-type]

    def test_model_dump_excludes_none_for_typed_dict_compat(self):
        """LangChain ``Decision`` TypedDict는 ``NotRequired`` 필드 — ``None``
        값을 키 자체로 보내면 안 된다 (router가 ``exclude_none=True``로 송신).
        """
        d = Decision(type="approve")
        dumped = d.model_dump(exclude_none=True)
        assert dumped == {"type": "approve"}
        assert "edited_action" not in dumped
        assert "message" not in dumped

        d2 = Decision(type="respond", message="ok")
        dumped2 = d2.model_dump(exclude_none=True)
        assert dumped2 == {"type": "respond", "message": "ok"}
        assert "edited_action" not in dumped2


class TestResumeRequestSchema:
    """``ResumeRequest`` dual-shape (contract §2.2, §2.3)."""

    def test_decisions_only_valid(self):
        """표준 경로: ``decisions``만 있으면 OK, ``response``=None."""
        req = ResumeRequest(decisions=[Decision(type="approve")])
        assert req.decisions is not None
        assert len(req.decisions) == 1
        assert req.response is None

    def test_response_str_only_valid(self):
        """legacy 경로: ``response`` 문자열만 있으면 OK."""
        req = ResumeRequest(response="hello")
        assert req.response == "hello"
        assert req.decisions is None

    def test_response_list_only_valid(self):
        """legacy 경로: ``response`` 문자열 리스트 (multi-select)."""
        req = ResumeRequest(response=["a", "b"])
        assert req.response == ["a", "b"]

    def test_response_dict_only_valid(self):
        """legacy 경로: ``response`` dict (builder edge case)."""
        req = ResumeRequest(response={"x": 1})
        assert req.response == {"x": 1}

    def test_neither_field_rejected_with_422_message(self):
        """둘 다 ``None``이면 ``_at_least_one`` validator가 ValidationError."""
        with pytest.raises(ValidationError, match="either 'decisions' or 'response'"):
            ResumeRequest()

    def test_both_present_accepted_for_transition_tolerance(self):
        """둘 다 들어와도 422 거절하지 않음 (transition window 관용).

        router가 ``decisions`` 우선 채택하므로 schema는 통과시킨다.
        """
        req = ResumeRequest(
            decisions=[Decision(type="approve")],
            response="legacy stuff",
        )
        # 둘 다 보존 — router 단계에서 표준 우선 분기.
        assert req.decisions is not None
        assert req.response == "legacy stuff"


# ---------------------------------------------------------------------------
# B. Router 변환 — POST /messages/resume → resume_agent_stream payload (§3, §2.4)
# ---------------------------------------------------------------------------


async def _seed_user_agent_conv() -> uuid.UUID:
    """User + Model + Agent + Conversation 한 세트 생성 후 conv_id 반환."""
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
    """``resume_agent_stream`` 호출 시 전달된 (positional args, kwargs) 캡처용
    side_effect를 만든다.

    반환: ``(captured_args_list, side_effect_fn)``.
    side_effect_fn은 minimal SSE chunk를 yield하는 async generator를 반환.
    """
    captured: list[Any] = []

    async def fake_stream(*args, **kwargs):
        captured.append(args)
        captured.append(kwargs)
        # router의 _sse_handler는 chunk가 한 개라도 emit돼야 200 응답.
        yield 'event: message_end\ndata: {"content": "ok", "usage": {}}\n\n'

    return captured, fake_stream


class TestResumeRouterPayload:
    """``POST /api/conversations/{id}/messages/resume`` 경로 검증.

    공통 패턴:
    - ``app.routers.conversations.resume_agent_stream`` 을 mock으로 교체해
      router가 전달한 ``(cfg, resume_payload, **kwargs)`` 를 캡처.
    - ``cfg`` 는 검사하지 않음 (Phase 1 가드 영역). 두 번째 positional arg인
      ``resume_payload`` 만 검증.
    """

    @pytest.mark.asyncio
    async def test_decisions_passed_through_as_command_resume_payload(
        self, client: AsyncClient
    ):
        """body ``{decisions: [...]}`` → ``resume_agent_stream(cfg, {"decisions": [...]})``."""
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
        # 첫 호출의 positional args = captured[0], kwargs = captured[1].
        args = captured[0]
        # args[0] = cfg, args[1] = resume_payload (router가 dict로 변환).
        payload = args[1]
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
        }, "router는 표준 decisions를 그대로 dict로 직렬화해 dict 페이로드로 송신해야 함"

    @pytest.mark.asyncio
    async def test_legacy_response_str_converted_to_single_respond_decision(
        self, client: AsyncClient
    ):
        """body ``{response: "yes"}`` → ``{"decisions":[{"type":"respond","message":"yes"}]}``."""
        conv_id = await _seed_user_agent_conv()
        captured, fake = _capture_resume_payload()

        with patch(
            "app.routers.conversations.resume_agent_stream", side_effect=fake
        ):
            resp = await client.post(
                f"/api/conversations/{conv_id}/messages/resume",
                json={"response": "yes"},
            )

        assert resp.status_code == 200
        args = captured[0]
        payload = args[1]
        assert payload == {
            "decisions": [{"type": "respond", "message": "yes"}]
        }

    @pytest.mark.asyncio
    async def test_legacy_response_list_joined_with_comma_space(
        self, client: AsyncClient
    ):
        """legacy multi-select: ``["a","b"]`` → ``"a, b"`` (§2.4)."""
        conv_id = await _seed_user_agent_conv()
        captured, fake = _capture_resume_payload()

        with patch(
            "app.routers.conversations.resume_agent_stream", side_effect=fake
        ):
            resp = await client.post(
                f"/api/conversations/{conv_id}/messages/resume",
                json={"response": ["alpha", "beta"]},
            )

        assert resp.status_code == 200
        payload = captured[0][1]
        assert payload == {
            "decisions": [{"type": "respond", "message": "alpha, beta"}]
        }

    @pytest.mark.asyncio
    async def test_legacy_response_dict_serialized_as_json(
        self, client: AsyncClient
    ):
        """legacy dict 응답: ``{"x":1}`` → ``json.dumps`` (§2.4)."""
        conv_id = await _seed_user_agent_conv()
        captured, fake = _capture_resume_payload()

        with patch(
            "app.routers.conversations.resume_agent_stream", side_effect=fake
        ):
            resp = await client.post(
                f"/api/conversations/{conv_id}/messages/resume",
                json={"response": {"x": 1, "name": "한글"}},
            )

        assert resp.status_code == 200
        payload = captured[0][1]
        assert payload["decisions"][0]["type"] == "respond"
        # json.dumps(..., ensure_ascii=False) 보장 — 한글이 escape되지 않음.
        message = payload["decisions"][0]["message"]
        parsed = json.loads(message)
        assert parsed == {"x": 1, "name": "한글"}
        assert "한글" in message, "ensure_ascii=False로 한글이 escape되지 않아야 함"

    @pytest.mark.asyncio
    async def test_empty_body_returns_422(self, client: AsyncClient):
        """``{}`` → schema validator가 422 (둘 다 None 거절)."""
        conv_id = await _seed_user_agent_conv()
        resp = await client.post(
            f"/api/conversations/{conv_id}/messages/resume",
            json={},
        )
        assert resp.status_code == 422
        assert "decisions" in resp.text or "response" in resp.text

    @pytest.mark.asyncio
    async def test_both_decisions_and_response_uses_decisions(
        self, client: AsyncClient
    ):
        """둘 다 들어오면 ``decisions`` 우선, ``response``는 무시 (§2.3 transition)."""
        conv_id = await _seed_user_agent_conv()
        captured, fake = _capture_resume_payload()

        with patch(
            "app.routers.conversations.resume_agent_stream", side_effect=fake
        ):
            resp = await client.post(
                f"/api/conversations/{conv_id}/messages/resume",
                json={
                    "decisions": [{"type": "approve"}],
                    "response": "legacy ignored",
                },
            )

        assert resp.status_code == 200
        payload = captured[0][1]
        assert payload == {"decisions": [{"type": "approve"}]}, (
            "둘 다 있을 때 표준 decisions만 채택, legacy response는 변환되지 않아야 함"
        )


# ---------------------------------------------------------------------------
# C/D. Streaming dual emit (§4)
# ---------------------------------------------------------------------------


def _make_intr(ns: str, value: Any) -> MagicMock:
    """``task.interrupts[i]`` 한 항목을 흉내내는 MagicMock.

    LangGraph internal: ``intr.ns`` (str) + ``intr.value`` (any). streaming.py
    가 ``getattr(intr, "ns", "")`` 와 ``intr.value``로만 접근하므로 두 속성
    있으면 충분 (langchain internal에 노출된 표면 area에 의존 — Phase 1
    test_hitl_middleware.py와 동일 brittleness).
    """
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
    """``astream``이 ``GraphInterrupt`` 던지고 ``aget_state``는 주어진 state
    반환하는 fake agent. ``stream_agent_response``의 dual emit 경로 테스트.
    """

    def __init__(self, state: MagicMock | Exception):
        self._state = state

    async def astream(self, *args, **kwargs):
        # GraphInterrupt 발생 — interrupt() 호출에 의한 정상 일시정지 시뮬레이션.
        from langgraph.errors import GraphInterrupt

        # generator가 되려면 yield가 있어야 함.
        if False:
            yield  # pragma: no cover
        # GraphInterrupt(interrupts=...): 빈 시퀀스로 충분 (테스트는 except
        # 분기만 검증). pyright argument typing 만족용 빈 list.
        raise GraphInterrupt([])

    async def aget_state(self, config: Any):
        if isinstance(self._state, Exception):
            raise self._state
        return self._state


def _parse_interrupt_events(events: list[str]) -> list[dict[str, Any]]:
    """``stream_agent_response`` 출력에서 ``event: interrupt`` 페이로드만 추출."""
    out: list[dict[str, Any]] = []
    for raw in events:
        if "event: interrupt\n" not in raw:
            continue
        # SSE: "event: interrupt\nid: ...\ndata: {...}\n\n"
        for line in raw.split("\n"):
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: ") :]))
    return out


class TestStreamingDualEmit:
    """``stream_agent_response``의 INTERRUPT chunk dual emit (§4)."""

    @pytest.mark.asyncio
    async def test_dual_emit_standard_then_legacy_with_same_interrupt_id(self):
        """표준 HITLRequest shape (action_requests/review_configs)이 도착하면
        표준 chunk + legacy chunk 두 개를 순서대로 emit, 동일 ``interrupt_id``.
        """
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

        assert len(intrs) == 2, (
            "한 interrupt당 정확히 두 chunk (표준 1 + legacy 1) 발행해야 함 — §4.3"
        )
        # 순서: 표준 먼저, legacy 나중 (frontend dedup 정책 §5.2 전제).
        std, legacy = intrs[0], intrs[1]
        assert "action_requests" in std and "review_configs" in std, (
            "첫 chunk는 표준 chunk (action_requests/review_configs 키) — §4.3"
        )
        assert std["action_requests"] == intr_value["action_requests"]
        assert std["review_configs"] == intr_value["review_configs"]
        assert "value" in legacy and "action_requests" not in legacy, (
            "두 번째 chunk는 legacy chunk (value 키) — §4.3"
        )
        # 같은 interrupt_id — frontend dedup 키.
        assert std["interrupt_id"] == "ns-42"
        assert legacy["interrupt_id"] == "ns-42"
        assert std["interrupt_id"] == legacy["interrupt_id"], (
            "두 chunk의 interrupt_id는 동일해야 함 (dedup 키) — §4.3, §4.1"
        )

    @pytest.mark.asyncio
    async def test_legacy_only_emit_when_value_lacks_standard_keys(self):
        """``intr.value``가 LangChain HITLRequest shape이 아닐 때 (자체
        ``ask_user.py`` 발행 케이스) 표준 chunk는 skip, legacy chunk만 emit
        — 회귀 0 보장 (§4.4).
        """
        # ask_user 자체 interrupt는 question/options 등 자체 키.
        intr_value = {"type": "select", "question": "Choose?", "options": ["a", "b"]}
        state = _make_state_with_interrupts([_make_intr("ns-ask-1", intr_value)])
        agent = _InterruptingAgent(state)

        events = [e async for e in stream_agent_response(agent, [], {})]
        intrs = _parse_interrupt_events(events)

        assert len(intrs) == 1, (
            "자체 ask_user interrupt는 legacy chunk 단독 (표준 chunk skip) — §4.4"
        )
        assert "value" in intrs[0] and "action_requests" not in intrs[0]
        assert intrs[0]["value"] == intr_value
        assert intrs[0]["interrupt_id"] == "ns-ask-1"

    @pytest.mark.asyncio
    async def test_fallback_emits_legacy_chunk_only_when_aget_state_fails(self):
        """``aget_state`` 실패 + ``was_interrupted=True`` → legacy chunk 단독 emit
        (§4.5). 표준 shape을 구성할 state 정보가 없으므로 legacy로 fallback.
        """
        agent = _InterruptingAgent(RuntimeError("aget_state boom"))

        events = [e async for e in stream_agent_response(agent, [], {})]
        intrs = _parse_interrupt_events(events)

        assert len(intrs) == 1, "fallback 분기는 legacy chunk 1개만 — §4.5"
        legacy = intrs[0]
        assert "action_requests" not in legacy
        assert legacy["interrupt_id"] == ""
        assert legacy["value"] == {"message": "Interrupt detected but state unavailable"}
