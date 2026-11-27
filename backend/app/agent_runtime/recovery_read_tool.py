"""Confirm a local read before LangChain makes its tool-end event observable."""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForToolRun
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

type ReadResultHandler = Callable[[str, Any], Awaitable[None]]

_before_read_return: ContextVar[ReadResultHandler | None] = ContextVar(
    "moldy_before_read_return", default=None
)


@contextmanager
def before_read_return(handler: ReadResultHandler | None) -> Iterator[None]:
    token = _before_read_return.set(handler)
    try:
        yield
    finally:
        _before_read_return.reset(token)


class RecoverySafeReadTool(StructuredTool):
    """A reviewed local read with no external effects, credentials or dynamic dispatch."""

    async def _arun(
        self,
        *args: Any,
        config: RunnableConfig,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
        **kwargs: Any,
    ) -> Any:
        result = await super()._arun(*args, config=config, run_manager=run_manager, **kwargs)
        callback = _before_read_return.get()
        if callback is not None:
            # BaseTool.arun calls on_tool_end after this returns but before the outer
            # agent middleware regains control. Include only the typed tool result.
            await callback(self.name, result)
        return result
