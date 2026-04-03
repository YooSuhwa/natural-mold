from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agent_runtime.creation_agent import run_creation_conversation


class TestRunCreationConversation:
    """creation_agent.run_creation_conversation 함수 테스트."""

    @pytest.mark.asyncio
    @patch("app.agent_runtime.creation_agent.create_chat_model")
    async def test_phase1_response(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(
            content="프로젝트를 시작합니다.\n```json\n"
            '{"current_phase": 2, '
            '"phase_result": "뉴스 모니터링 에이전트 초기화 완료", '
            '"question": "어떤 주제를 원하시나요?", '
            '"suggested_replies": {"options": ["경제", "기술", "직접 입력"], '
            '"multi_select": false}}\n```'
        )
        mock_create.return_value = mock_model

        result = await run_creation_conversation(
            conversation_history=[],
            user_message="뉴스 모니터링 에이전트 만들어줘",
        )

        assert result["current_phase"] == 2
        assert result["phase_result"] is not None
        assert result["question"] == "어떤 주제를 원하시나요?"
        assert result["suggested_replies"] is not None
        assert "경제" in result["suggested_replies"]["options"]
        assert result["role"] == "assistant"

    @pytest.mark.asyncio
    @patch("app.agent_runtime.creation_agent.create_chat_model")
    async def test_phase2_question(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(
            content="```json\n"
            '{"current_phase": 2, '
            '"question": "결과물을 어떤 형식으로 받고 싶으세요?", '
            '"suggested_replies": {"options": ["요약", "표", "직접 입력"], '
            '"multi_select": false}}\n```'
        )
        mock_create.return_value = mock_model

        result = await run_creation_conversation(
            conversation_history=[
                {"role": "user", "content": "뉴스 에이전트"},
                {"role": "assistant", "content": "이전 응답"},
            ],
            user_message="기술 뉴스",
        )

        assert result["current_phase"] == 2
        assert result["question"] is not None
        assert result["draft_config"] is None

    @pytest.mark.asyncio
    @patch("app.agent_runtime.creation_agent.create_chat_model")
    async def test_phase3_tool_recommendation(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(
            content="도구를 추천합니다.\n```json\n"
            '{"current_phase": 3, '
            '"phase_result": "Phase 2 완료", '
            '"recommended_tools": ['
            '{"name": "Web Search", "description": "웹 검색"},'
            '{"name": "Web Scraper", "description": "웹 스크래핑"}'
            "]}\n```"
        )
        mock_create.return_value = mock_model

        result = await run_creation_conversation(
            conversation_history=[],
            user_message="도구 추천해줘",
        )

        assert result["current_phase"] == 3
        assert len(result["recommended_tools"]) == 2
        assert result["recommended_tools"][0]["name"] == "Web Search"

    @pytest.mark.asyncio
    @patch("app.agent_runtime.creation_agent.create_chat_model")
    async def test_phase4_draft_config(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(
            content="에이전트를 생성합니다.\n```json\n"
            '{"current_phase": 4, '
            '"phase_result": "Phase 3 완료", '
            '"draft_config": {'
            '"name": "뉴스 모니터", '
            '"description": "뉴스를 모니터링하는 에이전트", '
            '"system_prompt": "당신은 뉴스 모니터입니다.", '
            '"recommended_tool_names": ["Web Search"], '
            '"recommended_model": "GPT-4o", '
            '"is_ready": true'
            "}}\n```"
        )
        mock_create.return_value = mock_model

        result = await run_creation_conversation(
            conversation_history=[],
            user_message="좋아, 만들어줘",
        )

        assert result["current_phase"] == 4
        assert result["draft_config"] is not None
        assert result["draft_config"]["name"] == "뉴스 모니터"
        assert result["draft_config"]["is_ready"] is True

    @pytest.mark.asyncio
    @patch("app.agent_runtime.creation_agent.create_chat_model")
    async def test_no_json_in_response(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(content="JSON이 없는 응답입니다.")
        mock_create.return_value = mock_model

        result = await run_creation_conversation(
            conversation_history=[],
            user_message="안녕",
        )

        assert result["current_phase"] == 1
        assert result["draft_config"] is None
        assert result["suggested_replies"] is None
        assert result["recommended_tools"] == []

    @pytest.mark.asyncio
    @patch("app.agent_runtime.creation_agent.create_chat_model")
    async def test_suggested_replies_list_format(self, mock_create):
        """suggested_replies가 리스트로 올 경우 dict로 변환."""
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(
            content="```json\n"
            '{"current_phase": 2, '
            '"question": "?", '
            '"suggested_replies": ["옵션1", "옵션2"]}\n```'
        )
        mock_create.return_value = mock_model

        result = await run_creation_conversation(
            conversation_history=[],
            user_message="test",
        )

        assert result["suggested_replies"] is not None
        assert result["suggested_replies"]["options"] == ["옵션1", "옵션2"]
        assert result["suggested_replies"]["multi_select"] is False

    @pytest.mark.asyncio
    @patch("app.agent_runtime.creation_agent.create_chat_model")
    async def test_available_tools_appended(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(content='```json\n{"current_phase": 1}\n```')
        mock_create.return_value = mock_model

        await run_creation_conversation(
            conversation_history=[],
            user_message="test",
            available_tools=["Web Search", "Gmail"],
            available_models=["GPT-4o", "Claude"],
        )

        call_args = mock_model.ainvoke.call_args[0][0]
        system_content = call_args[0].content
        assert "Web Search, Gmail" in system_content
        assert "GPT-4o, Claude" in system_content

    @pytest.mark.asyncio
    @patch("app.agent_runtime.creation_agent.create_chat_model")
    async def test_no_available_tools(self, mock_create):
        """available_tools/models가 None이면 프롬프트에 추가 안 됨."""
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(content='```json\n{"current_phase": 1}\n```')
        mock_create.return_value = mock_model

        await run_creation_conversation(
            conversation_history=[],
            user_message="test",
            available_tools=None,
            available_models=None,
        )

        call_args = mock_model.ainvoke.call_args[0][0]
        system_content = call_args[0].content
        assert "사용 가능한 도구" not in system_content

    @pytest.mark.asyncio
    @patch("app.agent_runtime.creation_agent.create_chat_model")
    async def test_raw_content_preserved(self, mock_create):
        raw = '설명\n```json\n{"current_phase": 2, "question": "?"}\n```'
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(content=raw)
        mock_create.return_value = mock_model

        result = await run_creation_conversation(
            conversation_history=[],
            user_message="test",
        )

        assert result["raw_content"] == raw
        assert "```json" not in result["content"]

    @pytest.mark.asyncio
    @patch("app.agent_runtime.creation_agent.create_chat_model")
    async def test_conversation_history_passed_correctly(self, mock_create):
        mock_model = AsyncMock()
        mock_model.ainvoke.return_value = AsyncMock(
            content='```json\n{"current_phase": 2, "question": "?"}\n```'
        )
        mock_create.return_value = mock_model

        history = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "resp2"},
        ]

        await run_creation_conversation(
            conversation_history=history,
            user_message="msg3",
        )

        call_args = mock_model.ainvoke.call_args[0][0]
        # system(1) + history(4) + user(1) = 6
        assert len(call_args) == 6
