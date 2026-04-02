from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_builtin_tool_factory():
    """Test that all builtin tools can be created."""
    from app.agent_runtime.tool_factory import create_builtin_tool

    for name in ("Web Search", "Web Scraper", "Current DateTime"):
        tool = create_builtin_tool(name)
        assert tool is not None
        assert tool.name  # Has a name
        assert tool.description  # Has a description


@pytest.mark.asyncio
async def test_builtin_tool_unknown():
    """Unknown builtin tool raises ValueError."""
    from app.agent_runtime.tool_factory import create_builtin_tool

    with pytest.raises(ValueError, match="Unknown builtin tool"):
        create_builtin_tool("Nonexistent Tool")


@pytest.mark.asyncio
async def test_current_datetime_tool():
    """Current DateTime tool returns a formatted string."""
    from app.agent_runtime.tool_factory import create_builtin_tool

    tool = create_builtin_tool("Current DateTime")
    result = await tool.ainvoke({})
    assert "년" in result
    assert "월" in result
    assert "KST" in result
