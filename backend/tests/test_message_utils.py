from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent_runtime.message_utils import (
    convert_to_langchain_messages,
    extract_json_from_markdown,
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
        msgs = convert_to_langchain_messages([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
            {"role": "assistant", "content": "asst"},
        ])
        assert len(msgs) == 3
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[1], HumanMessage)
        assert isinstance(msgs[2], AIMessage)

    def test_unknown_role_skipped(self):
        msgs = convert_to_langchain_messages([
            {"role": "unknown", "content": "???"},
            {"role": "user", "content": "Hello"},
        ])
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
        content = (
            '```json\n{"name": "Agent"}\n```\n'
            'Some text\n'
            '```json\n{"model": "gpt-4o"}\n```'
        )
        result = extract_json_from_markdown(content)
        assert result == {"name": "Agent", "model": "gpt-4o"}

    def test_invalid_json_skipped(self):
        content = (
            '```json\n{invalid json}\n```\n'
            '```json\n{"valid": true}\n```'
        )
        result = extract_json_from_markdown(content)
        assert result == {"valid": True}

    def test_all_invalid_json_returns_none(self):
        content = '```json\n{broken\n```'
        result = extract_json_from_markdown(content)
        assert result is None

    def test_no_json_blocks_returns_none(self):
        content = "Just some plain text without any code blocks."
        result = extract_json_from_markdown(content)
        assert result is None

    def test_empty_string(self):
        result = extract_json_from_markdown("")
        assert result is None


class TestStripJsonBlocks:
    def test_removes_json_blocks(self):
        content = 'Before\n```json\n{"key": "value"}\n```\nAfter'
        result = strip_json_blocks(content)
        assert result == "Before\n\nAfter"

    def test_removes_multiple_blocks(self):
        content = (
            'Start\n```json\n{"a": 1}\n```\n'
            'Middle\n```json\n{"b": 2}\n```\n'
            'End'
        )
        result = strip_json_blocks(content)
        assert "json" not in result
        assert "Start" in result
        assert "Middle" in result
        assert "End" in result

    def test_no_blocks_returns_original(self):
        content = "No code blocks here."
        result = strip_json_blocks(content)
        assert result == "No code blocks here."
