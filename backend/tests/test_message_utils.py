from __future__ import annotations

import uuid

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent_runtime.message_utils import (
    convert_to_langchain_messages,
    extract_json_from_markdown,
    langchain_messages_to_response,
    strip_json_blocks,
)


class TestConvertToLangchainMessages:
    def test_system_message(self):
        msgs = convert_to_langchain_messages([{"role": "system", "content": "You are helpful."}])
        assert len(msgs) == 1
        assert isinstance(msgs[0], SystemMessage)
        assert msgs[0].content == "You are helpful."

    def test_user_message(self):
        msgs = convert_to_langchain_messages([{"role": "user", "content": "Hello"}])
        assert len(msgs) == 1
        assert isinstance(msgs[0], HumanMessage)
        assert msgs[0].content == "Hello"

    def test_assistant_message(self):
        msgs = convert_to_langchain_messages([{"role": "assistant", "content": "Hi there"}])
        assert len(msgs) == 1
        assert isinstance(msgs[0], AIMessage)
        assert msgs[0].content == "Hi there"

    def test_mixed_roles(self):
        msgs = convert_to_langchain_messages(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "usr"},
                {"role": "assistant", "content": "asst"},
            ]
        )
        assert len(msgs) == 3
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[1], HumanMessage)
        assert isinstance(msgs[2], AIMessage)

    def test_unknown_role_skipped(self):
        msgs = convert_to_langchain_messages(
            [
                {"role": "unknown", "content": "???"},
                {"role": "user", "content": "Hello"},
            ]
        )
        assert len(msgs) == 1
        assert isinstance(msgs[0], HumanMessage)

    def test_empty_list(self):
        msgs = convert_to_langchain_messages([])
        assert msgs == []


class TestExtractJsonFromMarkdown:
    def test_single_json_block(self):
        content = 'Here is data:\n```json\n{"name": "Agent", "model": "gpt-4o"}\n```'
        result = extract_json_from_markdown(content)
        assert result == {"name": "Agent", "model": "gpt-4o"}

    def test_multiple_json_blocks_merged(self):
        content = '```json\n{"name": "Agent"}\n```\nSome text\n```json\n{"model": "gpt-4o"}\n```'
        result = extract_json_from_markdown(content)
        assert result == {"name": "Agent", "model": "gpt-4o"}

    def test_invalid_json_skipped(self):
        content = '```json\n{invalid json}\n```\n```json\n{"valid": true}\n```'
        result = extract_json_from_markdown(content)
        assert result == {"valid": True}

    def test_all_invalid_json_returns_none(self):
        content = "```json\n{broken\n```"
        result = extract_json_from_markdown(content)
        assert result is None

    def test_no_json_blocks_returns_none(self):
        content = "Just some plain text without any code blocks."
        result = extract_json_from_markdown(content)
        assert result is None

    def test_empty_string(self):
        result = extract_json_from_markdown("")
        assert result is None


class TestLangchainMessagesToResponse:
    """Anthropic이 list-of-blocks content를 반환할 때 응답 변환이 text만 추출하는지 검증.

    회귀 방지: 이전 구현은 `str(list)`로 직렬화해 사용자에게 raw dict repr이 노출됐다.
    """

    def test_string_content_passthrough(self):
        conv_id = uuid.uuid4()
        result = langchain_messages_to_response([AIMessage(content="안녕하세요")], conv_id)
        assert len(result) == 1
        assert result[0].role == "assistant"
        assert result[0].content == "안녕하세요"

    def test_anthropic_list_content_text_blocks_only(self):
        """text 블록만 concat. tool_use 블록은 무시 (tool_calls로 별도 노출)."""
        conv_id = uuid.uuid4()
        msg = AIMessage(
            content=[
                {"type": "text", "text": "직원 정보를 검색해볼게요!", "index": 0},
                {
                    "type": "tool_use",
                    "id": "toolu_01ABC",
                    "name": "search_employees",
                    "input": {"query": "이상윤"},
                    "index": 1,
                },
            ]
        )
        result = langchain_messages_to_response([msg], conv_id)
        assert result[0].content == "직원 정보를 검색해볼게요!"

    def test_anthropic_multi_text_blocks_concatenated(self):
        conv_id = uuid.uuid4()
        msg = AIMessage(
            content=[
                {"type": "text", "text": "이상윤 님의 팀 정보:\n"},
                {"type": "text", "text": "- 소속: 제품기술팀"},
            ]
        )
        result = langchain_messages_to_response([msg], conv_id)
        assert result[0].content == "이상윤 님의 팀 정보:\n- 소속: 제품기술팀"

    def test_empty_list_content(self):
        conv_id = uuid.uuid4()
        msg = AIMessage(content=[])
        result = langchain_messages_to_response([msg], conv_id)
        assert result[0].content == ""

    def test_list_content_with_only_tool_use(self):
        """tool_use만 있는 응답(보조 텍스트 없음)은 빈 문자열."""
        conv_id = uuid.uuid4()
        msg = AIMessage(
            content=[
                {
                    "type": "tool_use",
                    "id": "toolu_01XYZ",
                    "name": "search_employees",
                    "input": {},
                }
            ]
        )
        result = langchain_messages_to_response([msg], conv_id)
        assert result[0].content == ""

    def test_text_block_without_text_field_skipped(self):
        """text 키가 없거나 비-string이면 스킵."""
        conv_id = uuid.uuid4()
        msg = AIMessage(
            content=[
                {"type": "text"},  # no text
                {"type": "text", "text": None},  # null
                {"type": "text", "text": "정상"},
            ]
        )
        result = langchain_messages_to_response([msg], conv_id)
        assert result[0].content == "정상"

    def test_tool_message_string_content(self):
        conv_id = uuid.uuid4()
        msg = ToolMessage(content="결과", tool_call_id="toolu_1")
        result = langchain_messages_to_response([msg], conv_id)
        assert result[0].role == "tool"
        assert result[0].content == "결과"
        assert result[0].tool_call_id == "toolu_1"


class TestStripJsonBlocks:
    def test_removes_json_blocks(self):
        content = 'Before\n```json\n{"key": "value"}\n```\nAfter'
        result = strip_json_blocks(content)
        assert result == "Before\n\nAfter"

    def test_removes_multiple_blocks(self):
        content = 'Start\n```json\n{"a": 1}\n```\nMiddle\n```json\n{"b": 2}\n```\nEnd'
        result = strip_json_blocks(content)
        assert "json" not in result
        assert "Start" in result
        assert "Middle" in result
        assert "End" in result

    def test_no_blocks_returns_original(self):
        content = "No code blocks here."
        result = strip_json_blocks(content)
        assert result == "No code blocks here."
