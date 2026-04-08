"""Tests for app.services.assistant_service — stream_assistant_message."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.assistant_service import stream_assistant_message


@pytest.mark.asyncio
async def test_stream_assistant_message():
    """stream_assistant_message yields chunks from stream_agent_response."""
    mock_agent = MagicMock()
    mock_db = AsyncMock()
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    thread_id = f"assistant_{agent_id}"

    chunks = [
        'event: message_start\ndata: {"id": "m1", "role": "assistant"}\n\n',
        'event: content_delta\ndata: {"delta": "Hello"}\n\n',
        'event: message_end\ndata: {"content": "Hello", "usage": {}}\n\n',
    ]

    async def mock_stream(agent, messages, config, **kwargs):
        for c in chunks:
            yield c

    with (
        patch(
            "app.services.assistant_service.build_assistant_agent",
            return_value=mock_agent,
        ) as mock_build,
        patch(
            "app.services.assistant_service.stream_agent_response",
            side_effect=mock_stream,
        ),
    ):
        collected = []
        async for chunk in stream_assistant_message(
            db=mock_db,
            agent_id=agent_id,
            user_id=user_id,
            thread_id=thread_id,
            user_message="시스템 프롬프트 수정해줘",
        ):
            collected.append(chunk)

    assert len(collected) == 3
    assert "message_start" in collected[0]
    assert "content_delta" in collected[1]
    assert "message_end" in collected[2]

    # Verify build_assistant_agent was called with correct args
    mock_build.assert_called_once_with(mock_db, agent_id, user_id, thread_id)
