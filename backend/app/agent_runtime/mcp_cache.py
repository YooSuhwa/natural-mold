"""Runtime MCP tool cache and retry wrapper."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

_CACHE: dict[str, tuple[float, list[BaseTool]]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
_CACHE_GUARD = asyncio.Lock()


def _clone_tool(tool: BaseTool) -> BaseTool:
    try:
        return tool.model_copy(deep=True)
    except Exception:  # noqa: BLE001
        try:
            return tool.model_copy()
        except Exception:  # noqa: BLE001
            return tool


def _clone_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [_clone_tool(tool) for tool in tools]


async def _lock_for_key(key: str) -> asyncio.Lock:
    async with _CACHE_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _LOCKS[key] = lock
        return lock


async def get_cached_mcp_tools(
    key: str,
    factory: Callable[[], Awaitable[list[BaseTool]]],
    *,
    ttl_seconds: float,
) -> list[BaseTool]:
    """Return cached MCP tools for ``key``, refreshing with ``factory`` on miss."""

    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached is not None and now - cached[0] < ttl_seconds:
        return _clone_tools(cached[1])

    lock = await _lock_for_key(key)
    async with lock:
        now = time.monotonic()
        cached = _CACHE.get(key)
        if cached is not None and now - cached[0] < ttl_seconds:
            return _clone_tools(cached[1])
        tools = await factory()
        _CACHE[key] = (time.monotonic(), _clone_tools(tools))
        return _clone_tools(tools)


async def clear_mcp_tool_cache() -> None:
    """Clear all cached MCP tool discovery results."""

    async with _CACHE_GUARD:
        _CACHE.clear()
        _LOCKS.clear()


class MCPToolWithRetry(BaseTool):
    """Wrap a LangChain MCP tool with bounded retry and timeout behavior."""

    _original_tool: BaseTool = PrivateAttr()
    _max_retries: int = PrivateAttr()
    _retry_delay: float = PrivateAttr()
    _timeout_seconds: float = PrivateAttr()

    def __init__(
        self,
        original_tool: BaseTool,
        *,
        max_retries: int = 2,
        retry_delay: float = 0.25,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            name=original_tool.name,
            description=original_tool.description or "",
            args_schema=original_tool.args_schema,
            return_direct=original_tool.return_direct,
            metadata=getattr(original_tool, "metadata", None),
        )
        self._original_tool = original_tool
        self._max_retries = max(1, max_retries)
        self._retry_delay = max(0.0, retry_delay)
        self._timeout_seconds = max(0.1, timeout_seconds)

    async def _arun(
        self,
        *args: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        last_error: BaseException | None = None
        payload = kwargs if kwargs else (args[0] if args and isinstance(args[0], dict) else {})
        for attempt in range(self._max_retries):
            try:
                return await asyncio.wait_for(
                    self._original_tool.ainvoke(payload, config=config),
                    timeout=self._timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt + 1 < self._max_retries and self._retry_delay > 0:
                    await asyncio.sleep(self._retry_delay)
        assert last_error is not None
        return f"[MCP Tool Error] {self.name}: {type(last_error).__name__}: {last_error}"

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        return asyncio.run(self._arun(*args, **kwargs))
