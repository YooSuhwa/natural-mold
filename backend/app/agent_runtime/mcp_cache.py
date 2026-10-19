"""Runtime MCP retry wrapper (the discovery cache lives in ``mcp_tool_cache``).

MCP 401/403 responses are typed (``McpAuthError``, F-12): they are never retried
and come back as an error ToolMessage with an ``mcp_auth_error`` artifact (see
``mcp_auth_context``); transport errors keep the bounded retry and the
``[MCP Tool Error]`` text.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

from app.agent_runtime.mcp_auth_context import McpAuthContext, auth_error_result
from app.agent_runtime.mcp_auth_errors import McpAuthError, classify_mcp_error
from app.agent_runtime.mcp_cache_invalidation import register_mcp_cache_invalidation
from app.agent_runtime.mcp_tool_cache import clear_mcp_tool_cache, get_cached_mcp_tools
from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.mcp.apps import sanitize_result_meta, sanitize_result_payload
from app.mcp.domain import McpAppRuntimeContext
from app.schemas.mcp_apps import McpAppArtifact

logger = logging.getLogger(__name__)

_MCP_RESULT_ENVELOPE = "__moldy_mcp_result__"
_ACTIVE_TOOL_CALL_ID: ContextVar[str | None] = ContextVar("moldy_mcp_tool_call_id", default=None)
_ACTIVE_RUN_ID: ContextVar[str | None] = ContextVar("moldy_mcp_run_id", default=None)
BindingRecorder = Callable[
    [
        McpAppRuntimeContext,
        str,
        str,
        dict[str, Any] | None,
        dict[str, Any] | None,
    ],
    Awaitable[str | None],
]

register_mcp_cache_invalidation()


class MCPToolWithRetry(BaseTool):
    """Wrap a LangChain MCP tool with bounded retry and timeout behavior."""

    _original_tool: BaseTool = PrivateAttr()
    _max_retries: int = PrivateAttr()
    _retry_delay: float = PrivateAttr()
    _timeout_seconds: float = PrivateAttr()
    _mcp_app_context: McpAppRuntimeContext | None = PrivateAttr(default=None)
    _binding_recorder: BindingRecorder | None = PrivateAttr(default=None)
    _auth_context: McpAuthContext | None = PrivateAttr(default=None)

    def __init__(
        self,
        original_tool: BaseTool,
        *,
        max_retries: int = 2,
        retry_delay: float = 0.25,
        timeout_seconds: float = 30.0,
        mcp_app_context: McpAppRuntimeContext | None = None,
        binding_recorder: BindingRecorder | None = None,
        auth_context: McpAuthContext | None = None,
    ) -> None:
        super().__init__(
            name=original_tool.name,
            description=original_tool.description or "",
            args_schema=original_tool.args_schema,
            return_direct=original_tool.return_direct,
            metadata=getattr(original_tool, "metadata", None),
            response_format=original_tool.response_format,
        )
        self._original_tool = original_tool
        self._max_retries = 1 if mcp_app_context is not None else max(1, max_retries)
        self._retry_delay = max(0.0, retry_delay)
        self._timeout_seconds = max(0.1, timeout_seconds)
        self._mcp_app_context = mcp_app_context
        self._binding_recorder = binding_recorder
        self._auth_context = auth_context

    async def ainvoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        """Capture the actual ToolCall id before BaseTool parses its arguments."""

        tool_call_id = input.get("id") if isinstance(input, dict) else None
        configurable = (config or {}).get("configurable", {})
        run_id = configurable.get("moldy_run_id") if isinstance(configurable, dict) else None
        call_token = _ACTIVE_TOOL_CALL_ID.set(
            tool_call_id if isinstance(tool_call_id, str) else None
        )
        run_token = _ACTIVE_RUN_ID.set(run_id if isinstance(run_id, str) else None)
        try:
            return await super().ainvoke(input, config=config, **kwargs)
        except McpAuthError as error:
            content, artifact = await auth_error_result(
                error, self._auth_context, self.name, run_id=_ACTIVE_RUN_ID.get()
            )
            if not isinstance(tool_call_id, str):
                return content
            return ToolMessage(
                content,
                artifact=artifact,
                tool_call_id=tool_call_id,
                name=self.name,
                status="error",
            )
        finally:
            _ACTIVE_RUN_ID.reset(run_token)
            _ACTIVE_TOOL_CALL_ID.reset(call_token)

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
                original_input: Any = payload
                if self.response_format == "content_and_artifact":
                    original_input = {
                        "name": self._original_tool.name,
                        "args": payload,
                        "id": "mcp-retry-internal",
                        "type": "tool_call",
                    }
                result = await asyncio.wait_for(
                    self._original_tool.ainvoke(original_input, config=config),
                    timeout=self._timeout_seconds,
                )
                if self.response_format == "content_and_artifact" and isinstance(
                    result, ToolMessage
                ):
                    artifact, result_meta = _unwrap_mcp_result_artifact(result.artifact)
                    app_artifact = await self._mcp_app_artifact(
                        result_meta=result_meta,
                        structured_content=(
                            artifact.get("structured_content")
                            if isinstance(artifact, dict)
                            and isinstance(artifact.get("structured_content"), dict)
                            else None
                        ),
                    )
                    if app_artifact is not None:
                        artifact = {**(artifact or {}), "mcp_app": app_artifact}
                    return result.content, artifact
                return result
            except Exception as exc:  # noqa: BLE001
                auth_error = classify_mcp_error(exc)
                if auth_error is not None:  # 401/403: never re-sent, typed for the runtime
                    # ``from None``: the httpx error carries the request URL; keep it out
                    # of any later traceback/exc_info log (kind/status stay on the error).
                    raise auth_error from None
                last_error = exc
                if attempt + 1 < self._max_retries and self._retry_delay > 0:
                    await asyncio.sleep(self._retry_delay)
        assert last_error is not None  # noqa: S101 — retry-loop invariant (type narrowing)
        message = f"[MCP Tool Error] {self.name}: {type(last_error).__name__}: {last_error}"
        return (message, None) if self.response_format == "content_and_artifact" else message

    async def _mcp_app_artifact(
        self,
        *,
        result_meta: dict[str, Any] | None,
        structured_content: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        context = self._mcp_app_context
        recorder = self._binding_recorder
        tool_call_id = _ACTIVE_TOOL_CALL_ID.get()
        run_id = _ACTIVE_RUN_ID.get()
        if (
            context is None
            or recorder is None
            or not isinstance(tool_call_id, str)
            or not isinstance(run_id, str)
        ):
            return None
        redacted_meta = redact_protocol_data(
            "tools/call",
            result_meta,
            redact_memory=False,
        )
        safe_result_meta = redacted_meta if isinstance(redacted_meta, dict) else None
        bounded_structured = sanitize_result_payload(structured_content)
        if structured_content is not None and not isinstance(bounded_structured, dict):
            return None
        redacted_structured = redact_protocol_data(
            "tools/call",
            bounded_structured,
            redact_memory=False,
        )
        safe_structured = redacted_structured if isinstance(redacted_structured, dict) else None
        try:
            binding_id = await recorder(
                context,
                run_id,
                tool_call_id,
                safe_result_meta,
                safe_structured,
            )
        except Exception:  # noqa: BLE001 - provenance failure keeps ordinary tool fallback
            logger.warning("MCP App provenance write failed", exc_info=True)
            return None
        if binding_id is None:
            return None
        try:
            artifact = McpAppArtifact.model_validate(
                {
                    "binding_id": binding_id,
                    "run_id": run_id,
                    "resource_uri": context.resource_uri,
                    "tool_meta": context.tool_meta,
                    "result_meta": safe_result_meta,
                }
            )
        except ValueError:
            logger.warning("MCP App provenance recorder returned an invalid binding reference")
            return None
        return artifact.model_dump(mode="json", exclude_none=True)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        return asyncio.run(self._arun(*args, **kwargs))


def _unwrap_mcp_result_artifact(
    artifact: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(artifact, dict):
        return None, None
    structured = artifact.get("structured_content")
    if not isinstance(structured, dict) or _MCP_RESULT_ENVELOPE not in structured:
        return artifact, None
    envelope = structured.get(_MCP_RESULT_ENVELOPE)
    if not isinstance(envelope, dict):
        return None, None
    original = envelope.get("structured_content")
    restored = {"structured_content": original} if isinstance(original, dict) else None
    return restored, sanitize_result_meta(envelope.get("result_meta"))


__all__ = [
    "MCPToolWithRetry",
    "_MCP_RESULT_ENVELOPE",
    "clear_mcp_tool_cache",
    "get_cached_mcp_tools",
]
