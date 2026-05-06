"""Builder v3 wire 통일 (Phase 5) 가드.

ADR-012 §Phase 5 — frontend ``decisionToBuilderResponse`` 어댑터를 retire하고
backend router 가 표준 ``Decision[]`` → builder native shape (dict | str) 변환을
담당하는 clean break 디자인의 회귀 가드.

검증 4축:
1. Router contract — 표준 ``decisions`` 페이로드 200 처리
2. Clean break 가드 — legacy ``response`` 필드는 422 (Pydantic ``extra='forbid'``)
3. Helper 매핑 — ``decisions_to_builder_response`` 5 케이스 단위
4. Phase 6 JSON.parse fallback — frontend 가 dict 의도로 JSON.stringify 한
   string 도 ``phase6_choice_wait`` / ``phase6_image_approval`` 가 정상 분기
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.builder_session import BuilderSession
from app.schemas.builder import BuilderStatus
from app.schemas.conversation import Decision
from app.services import builder_service
from tests.conftest import TEST_USER_ID

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _seed_session(db: AsyncSession) -> uuid.UUID:
    session = BuilderSession(
        user_id=TEST_USER_ID,
        user_request="phase5 wire 가드용 세션",
        status=BuilderStatus.BUILDING,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session.id


def _capture_resume_payload():
    """run_v3_resume_stream 호출 인자를 캡처하는 fake (SSE 스트림 mock)."""
    captured: list[dict] = []

    async def fake(**kwargs):
        captured.append(kwargs)
        # 빈 SSE stream — 라우터 통과만 검증
        yield "event: message_end\ndata: {}\n\n"

    return captured, fake


# ---------------------------------------------------------------------------
# A. Helper unit — decisions_to_builder_response
# ---------------------------------------------------------------------------


class TestDecisionsToBuilderResponse:
    def test_approve_maps_to_approved_dict(self):
        out = builder_service.decisions_to_builder_response(
            [Decision(type="approve")]
        )
        assert out == {"approved": True}

    def test_reject_with_message_carries_revision_message(self):
        out = builder_service.decisions_to_builder_response(
            [Decision(type="reject", message="이름 다시")]
        )
        assert out == {"approved": False, "revision_message": "이름 다시"}

    def test_reject_without_message_uses_empty_string(self):
        out = builder_service.decisions_to_builder_response(
            [Decision(type="reject")]
        )
        assert out == {"approved": False, "revision_message": ""}

    def test_respond_returns_message_string(self):
        out = builder_service.decisions_to_builder_response(
            [Decision(type="respond", message="옵션 A")]
        )
        assert out == "옵션 A"

    def test_edit_falls_back_to_approve(self):
        # builder graph 는 edit args 를 사용하지 않음 — approve fallback
        out = builder_service.decisions_to_builder_response(
            [
                Decision(
                    type="edit",
                    edited_action={"name": "x", "args": {"k": "v"}},
                )
            ]
        )
        assert out == {"approved": True}

    def test_empty_list_returns_none(self):
        assert builder_service.decisions_to_builder_response([]) is None


# ---------------------------------------------------------------------------
# B. Router contract — 표준 wire vs legacy
# ---------------------------------------------------------------------------


class TestResumeRouterContract:
    @pytest.mark.asyncio
    async def test_resume_accepts_standard_decisions(
        self, client: AsyncClient, db: AsyncSession
    ):
        """표준 ``{decisions: [{type:'respond', message:'옵션 A'}]}`` → 200 + builder
        helper 가 string ``"옵션 A"`` 로 변환해 graph 로 전달."""
        session_id = await _seed_session(db)
        captured, fake = _capture_resume_payload()

        with patch(
            "app.routers.builder.builder_service.run_v3_resume_stream",
            side_effect=fake,
        ):
            resp = await client.post(
                f"/api/builder/{session_id}/messages/resume",
                json={
                    "decisions": [{"type": "respond", "message": "옵션 A"}],
                    "interrupt_id": "intr-uuid-1",
                },
            )

        assert resp.status_code == 200
        assert len(captured) == 1
        # Helper 가 respond → string 으로 변환했는지 확인
        assert captured[0]["response"] == "옵션 A"
        assert captured[0]["interrupt_id"] == "intr-uuid-1"

    @pytest.mark.asyncio
    async def test_resume_approve_decision_maps_to_dict(
        self, client: AsyncClient, db: AsyncSession
    ):
        """approve Decision → ``{"approved": True}`` dict 로 변환되어 전달."""
        session_id = await _seed_session(db)
        captured, fake = _capture_resume_payload()

        with patch(
            "app.routers.builder.builder_service.run_v3_resume_stream",
            side_effect=fake,
        ):
            resp = await client.post(
                f"/api/builder/{session_id}/messages/resume",
                json={"decisions": [{"type": "approve"}]},
            )

        assert resp.status_code == 200
        assert captured[0]["response"] == {"approved": True}

    @pytest.mark.asyncio
    async def test_resume_rejects_legacy_response_field_422(
        self, client: AsyncClient, db: AsyncSession
    ):
        """Phase 5 clean break — legacy ``{response: "..."}`` 페이로드는 422.

        ``BuilderResumeRequest.model_config['extra'] = 'forbid'`` + ``decisions``
        Field(min_length=1) 강제. 향후 호환성 명목으로 dual-shape 추가 시도 차단.
        """
        session_id = await _seed_session(db)
        resp = await client.post(
            f"/api/builder/{session_id}/messages/resume",
            json={"response": "legacy"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_resume_empty_decisions_422(
        self, client: AsyncClient, db: AsyncSession
    ):
        """Decisions 빈 배열은 ``min_length=1`` 위반 → 422."""
        session_id = await _seed_session(db)
        resp = await client.post(
            f"/api/builder/{session_id}/messages/resume",
            json={"decisions": []},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# C. phase6 image — JSON string fallback
# ---------------------------------------------------------------------------


class TestPhase6JsonStringFallback:
    """phase6_choice_wait / phase6_image_approval 가 dict-의도 JSON string 도 처리.

    Phase 5 router 어댑터는 ``respond`` Decision 을 string body 로 변환해
    builder graph 에 전달하지만, frontend 에서 image_choice 의도를 dict 로
    구성한 뒤 ``JSON.stringify`` 한 경우에도 backward-compatible 하게 dict 분기로
    fallthrough 되어야 한다 (graph 변경 0 + helper 정규화 책임).
    """

    def test_parse_choice_response_json_object_string_promotes_to_dict(self):
        """``'{"choice":"skip","prompt":"p"}'`` → dict 분기."""
        from app.agent_runtime.builder_v3.nodes._helpers import (
            parse_choice_response,
        )

        choice, prompt = parse_choice_response('{"choice":"skip","prompt":"p"}')
        assert choice == "skip"
        assert prompt == "p"

    def test_parse_choice_response_json_with_auto_prompt_key(self):
        """image_choice 카드는 ``auto_prompt`` 키도 지원."""
        from app.agent_runtime.builder_v3.nodes._helpers import (
            parse_choice_response,
        )

        choice, prompt = parse_choice_response(
            '{"choice":"generate","auto_prompt":"a"}'
        )
        assert choice == "generate"
        assert prompt == "a"

    def test_parse_choice_response_invalid_json_falls_through_to_string(self):
        """JSON.parse 실패 시 평범한 string 옵션으로 취급 — 기존 동작 보존."""
        from app.agent_runtime.builder_v3.nodes._helpers import (
            parse_choice_response,
        )

        choice, prompt = parse_choice_response("{not-json}")
        # 잘못된 JSON 은 lower() 처리된 원본 string 그대로 반환
        assert choice == "{not-json}"
        assert prompt == ""

    def test_parse_choice_response_preserves_dict_and_plain_string(self):
        """dict 직접 입력 / plain string 옵션 — 기존 분기 보존 (회귀 가드)."""
        from app.agent_runtime.builder_v3.nodes._helpers import (
            parse_choice_response,
        )

        # dict 직접 입력
        c1, p1 = parse_choice_response({"choice": "SKIP", "prompt": "x"})
        assert c1 == "skip"
        assert p1 == "x"

        # plain string 옵션 라벨 — JSON 형태 아니므로 lower() 만 적용
        c2, p2 = parse_choice_response("skip")
        assert c2 == "skip"
        assert p2 == ""

    def test_parse_choice_response_prompt_keys_ordering(self):
        """image_approval 은 ``prompt_keys=("prompt",)`` 로 ``auto_prompt`` 무시."""
        from app.agent_runtime.builder_v3.nodes._helpers import (
            parse_choice_response,
        )

        choice, prompt = parse_choice_response(
            {"choice": "regenerate", "auto_prompt": "ignored"},
            prompt_keys=("prompt",),
        )
        assert choice == "regenerate"
        assert prompt == ""

    @pytest.mark.asyncio
    async def test_phase6_choice_wait_node_accepts_json_string(self, monkeypatch):
        """phase6_choice_wait 노드 자체가 JSON string 을 받아 skip 분기로 진입.

        ``langgraph.types.interrupt`` 호출을 mocking 해서 JSON string 응답을
        주입한 뒤 노드가 image_skipped=True + current_phase=7 로 전이하는지 검증.
        """
        from app.agent_runtime.builder_v3.nodes import phase6_image

        # interrupt 가 JSON 객체 string 을 반환하도록 monkeypatch
        def _fake_interrupt(_payload):
            return '{"choice":"skip","prompt":"unused"}'

        monkeypatch.setattr(phase6_image, "interrupt", _fake_interrupt)

        state = {
            "messages": [],
            "session_id": "s1",
            "intent": {
                "agent_name_ko": "테스트",
                "agent_description": "d",
                "primary_task_type": "x",
            },
            "todos": None,
            "pending_tool_call_id": "tc-skip-json",
        }

        result = await phase6_image.phase6_choice_wait(state)  # type: ignore[arg-type]
        assert result["current_phase"] == 7
        assert result["image_skipped"] is True
        assert result["pending_tool_call_id"] is None
