"""Fence model and tool dispatch on ordinary chat graphs eligible for worker recovery."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.agent_runtime.recovery_read_tool import RecoverySafeReadTool, before_read_return
from app.chat_recovery.fence import RunFence, current_run_fence, lock_run_fence, open_run_session
from app.chat_recovery.identity import recovery_digest
from app.chat_recovery.receipts import (
    ToolDispatch,
    ToolRecoveryBlocked,
    confirm_dispatch,
    reserve_dispatch,
)
from app.tools.keys import ref_of

_MAX_RESULT_BYTES = 32_768


def _require_fence() -> RunFence:
    fence = current_run_fence()
    if fence is None:
        raise ToolRecoveryBlocked("tool_receipt_conflict")
    return fence


def _dispatch(request: ToolCallRequest, fence: RunFence) -> ToolDispatch:
    call = request.tool_call
    call_id, name = call.get("id"), call.get("name")
    if not call_id or not name or len(call_id) > 255 or len(name) > 255:
        raise ToolRecoveryBlocked("tool_receipt_conflict")
    # The same short id may be emitted twice by a provider. Bind it to the
    # originating AI message, the registered tool identity and the original args.
    messages = request.state.get("messages", ()) if isinstance(request.state, Mapping) else ()
    issuer = next((m.id for m in reversed(messages) if isinstance(m, AIMessage)), None)
    tool_key = ref_of(request.tool).key if request.tool is not None else None
    identity = [str(fence.run_id), issuer, tool_key, name, call.get("args")]
    try:
        digest = recovery_digest(fence.tenant.tenant_id, "tool-arguments", identity)
    except (TypeError, ValueError) as exc:
        raise ToolRecoveryBlocked("tool_receipt_conflict") from exc
    return ToolDispatch(
        call_id,
        name,
        digest,
        isinstance(request.tool, RecoverySafeReadTool),
    )


def _stored_result(call: ToolDispatch, message: ToolMessage | Command) -> dict[str, Any] | None:
    # Persist only simple, bounded results from the two reviewed builtins. Never
    # persist a Command, a credential-bearing tool result or privileged metadata.
    if not call.read_only or not isinstance(message, ToolMessage) or message.status != "success":
        return None
    if message.tool_call_id != call.call_id or (message.name and message.name != call.tool_name):
        return None
    if message.artifact is not None or message.response_metadata or message.additional_kwargs:
        return None
    if not isinstance(message.content, str) or len(message.content.encode()) > _MAX_RESULT_BYTES:
        return None
    payload = message.model_dump(mode="json", include={"id", "name", "content", "tool_call_id"})
    if redact_protocol_data("messages", payload) != payload:
        return None
    return payload


async def _confirm(fence: RunFence, call: ToolDispatch, result: ToolMessage | Command) -> None:
    try:
        await confirm_dispatch(fence, call, _stored_result(call, result))
    except GraphBubbleUp:
        raise
    except Exception as exc:
        # The acknowledgement may have been lost after a remote action or DB commit.
        code = "tool_result_unavailable" if call.read_only else "recovery_effect_unconfirmed"
        raise ToolRecoveryBlocked(code) from exc


async def _require_owner(fence: RunFence) -> None:
    async with open_run_session(fence) as db:
        await lock_run_fence(db, fence)


class RecoveryToolMiddleware(AgentMiddleware):
    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        await _require_owner(_require_fence())
        return await handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        fence = _require_fence()
        call = _dispatch(request, fence)
        cached = await reserve_dispatch(fence, call)
        if cached is not None:
            if (
                set(cached) != {"id", "name", "content", "tool_call_id"}
                or cached.get("tool_call_id") != call.call_id
                or cached.get("name") not in (None, call.tool_name)
                or not isinstance(cached.get("content"), str)
            ):
                raise ToolRecoveryBlocked("tool_result_unavailable")
            return ToolMessage.model_validate(cached)
        recorded = False

        async def record_read(name: str, value: Any) -> None:
            nonlocal recorded
            if name != call.tool_name or not isinstance(value, str):
                raise ToolRecoveryBlocked("tool_result_unavailable")
            await _confirm(
                fence, call, ToolMessage(name=name, tool_call_id=call.call_id, content=value)
            )
            recorded = True

        # The worker may have stopped between committing intent and dispatch.
        await _require_owner(fence)
        try:
            with before_read_return(record_read if call.read_only else None):
                response = await handler(request)
        except GraphBubbleUp:
            # Preserve approvals and ownership cancellation exactly.
            raise
        except Exception as exc:
            if call.read_only:
                raise
            raise ToolRecoveryBlocked("recovery_effect_unconfirmed") from exc
        # Even an error reported by the external adapter can follow a real send.
        # Stop here before the model can propose another dispatch under a new id.
        if not call.read_only and isinstance(response, ToolMessage) and response.status == "error":
            raise ToolRecoveryBlocked("recovery_effect_unconfirmed")
        if not recorded:
            await _confirm(fence, call, response)
        return response


def with_recovery_fence(
    middleware: list[Any], children: list[dict[str, Any]]
) -> tuple[list[Any], list[dict[str, Any]]]:
    if current_run_fence() is None:
        return middleware, children
    guarded = []
    for spec in children:
        if "runnable" in spec or "graph_id" in spec:
            # The enclosing task dispatch has an external-effect receipt. A worker
            # cannot replay an interrupted delegation into an uninstrumented graph.
            guarded.append(spec)
        else:
            stack: Sequence[Any] = spec.get("middleware") or ()
            guarded.append({**spec, "middleware": [*stack, RecoveryToolMiddleware()]})
    return [*middleware, RecoveryToolMiddleware()], guarded
