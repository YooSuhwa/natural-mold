"""Root model context and tool restrictions for a server-admitted conversation goal."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from inspect import unwrap
from typing import Any

from deepagents.middleware import filesystem
from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command

from app.agent_runtime.filesystem_permissions import UnsafeFilesystemPath, canonicalize_virtual_path
from app.config import settings
from app.services.artifact_paths import ArtifactPathError, validate_artifact_relative_path

# These are product-owned read operations selected by their factory keys, never model aliases.
GOAL_READ_BUILTINS = frozenset(
    {
        "builtin:web_search",
        "builtin:web_scraper",
        "builtin:current_datetime",
        "builtin:resolve_relative_date",
        "builtin:e2e_scripted_search",
        "builtin:e2e_ui_data_demo",
    }
)
_FS_READ = frozenset({"read_file", "ls", "glob", "grep"})
_FS_WRITE = frozenset({"write_file", "edit_file"})
type ToolResult = ToolMessage | Command[Any]


def readable_goal_tool_configs(configs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    # Discovery may start a local server. Exclude nonselected sources before construction.
    return [
        config
        for config in configs
        if not config.get("mcp_server_url") and config.get("definition_key") in GOAL_READ_BUILTINS
    ]


def goal_setup_allowed(runtime_profile: str, configs: Iterable[Mapping[str, Any]]) -> bool:
    # ShellToolMiddleware can open a session before the model ever calls a tool. Unknown
    # configurations must not be initialized in an automatic run. Keep safety checks intact.
    permitted = {
        "summarization",
        "context_editing",
        "filesystem",
        "todo_list",
        "subagent",
        "human_in_the_loop",
        "pii",
        "llm_tool_selector",
        "model_call_limit",
        "tool_retry",
        "tool_call_limit",
        "model_fallback",
        "model_retry",
        "anthropic_prompt_caching",
        "openai_moderation",
    }
    return runtime_profile == "standard" and all(
        config.get("type") in permitted for config in configs
    )


def _filesystem_tool(tool: BaseTool) -> bool:
    """Only the installed filesystem implementation (including Moldy's permission wrapper).

    User-configured registry tools and MCP servers can choose their own names. Checking the
    tool object's handler prevents an alias such as `read_file` from acquiring this authority.
    """
    if tool.name not in _FS_READ | _FS_WRITE or not isinstance(tool, StructuredTool):
        return False
    expected = f"FilesystemMiddleware._create_{tool.name}_tool.<locals>."
    handlers = [fn for fn in (tool.func, tool.coroutine) if fn is not None]
    return bool(handlers) and all(
        getattr(unwrap(fn), "__globals__", None) is vars(filesystem)
        and unwrap(fn).__qualname__.startswith(expected)
        for fn in handlers
    )


class GoalRunMiddleware(AgentMiddleware):
    def __init__(
        self,
        thread_id: str,
        context: str | None,
        *,
        automatic: bool,
        read_tools: Iterable[BaseTool],
    ) -> None:
        super().__init__()
        self.context = context
        self.automatic = automatic
        self.prefix = f"/conversations/{thread_id}/"
        self._read_tools = tuple(read_tools)

    def _visible(self, tool: object) -> bool:
        return any(tool is item for item in self._read_tools) or (
            isinstance(tool, BaseTool) and _filesystem_tool(tool)
        )

    def _request(self, request: ModelRequest) -> ModelRequest:
        if self.context:
            messages = list(request.messages)
            index = next(
                (
                    i
                    for i in range(len(messages) - 1, -1, -1)
                    if isinstance(messages[i], HumanMessage)
                ),
                0,
            )
            messages.insert(
                index, HumanMessage(name="moldy_conversation_goal", content=self.context)
            )
            request = request.override(messages=messages)
        if self.automatic:
            request = request.override(
                tools=[tool for tool in request.tools if self._visible(tool)]
            )
        return request

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        return handler(self._request(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        return await handler(self._request(request))

    def _allowed(self, request: ToolCallRequest) -> bool:
        if not self.automatic:
            return True
        tool = request.tool
        if tool is None or tool.name != request.tool_call.get("name") or not self._visible(tool):
            return False
        if tool.name not in _FS_WRITE:
            return True
        args = request.tool_call["args"]
        if tool.name == "write_file":
            content = args.get("content")
            if (
                not isinstance(content, str)
                or len(content.encode("utf-8")) > settings.artifact_max_bytes
            ):
                return False
        path = args.get("file_path")
        if not isinstance(path, str) or path.endswith("/"):
            return False
        try:
            path = canonicalize_virtual_path(path)
            if not path.startswith(self.prefix):
                return False
            validate_artifact_relative_path(path[len(self.prefix) :])
        except (UnsafeFilesystemPath, ArtifactPathError):
            return False
        return True

    def _refusal(self, request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="Tool call denied: goal_followup_tool_restricted",
            status="error",
            tool_call_id=request.tool_call.get("id") or "",
        )

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolResult]
    ) -> ToolResult:
        return handler(request) if self._allowed(request) else self._refusal(request)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolResult]]
    ) -> ToolResult:
        return await handler(request) if self._allowed(request) else self._refusal(request)
