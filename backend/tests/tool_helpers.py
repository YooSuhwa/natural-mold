from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain_core.tools import BaseTool


def tool_coroutine(tool: BaseTool) -> Callable[..., Awaitable[Any]]:
    coroutine = getattr(tool, "coroutine", None)
    assert callable(coroutine)
    return cast(Callable[..., Awaitable[Any]], coroutine)
