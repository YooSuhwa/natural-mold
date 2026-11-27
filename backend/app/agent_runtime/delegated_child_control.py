"""Route a conversation instruction to one live, authorized subagent invocation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Annotated, Any, NotRequired
from uuid import UUID

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain.agents.middleware.types import (
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.runtime import Runtime
from langgraph.types import Command
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.delegated_child_checkpoint import (
    child_instruction_message_id,
    saved_child_step,
)
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.model_input_receipt import observe_message_delivery, text_digest
from app.agent_runtime.protocol_redaction import redact_protocol_data
from app.authz.org_context import OrgContext
from app.child_tasks import persistence as store
from app.child_tasks.errors import ChildTaskStopped
from app.config import settings
from app.database import tenant_session
from app.exceptions import AppError
from app.schemas.conversation_graph_input import CHILD_RECEIPTS_CHANNEL, STEER_INPUT_MARKER

type ToolResult = ToolMessage | Command[Any]
type ToolHandler = Callable[[ToolCallRequest], Awaitable[ToolResult]]


@dataclass(slots=True)
class _Invocation:
    lifecycle: ChildTaskLifecycle
    child_id: UUID
    run_id: UUID
    tool_call_id: str
    namespace: str
    sent: dict[UUID, str] = field(default_factory=dict)


_invocation: ContextVar[_Invocation | ChildTaskLifecycle | None] = ContextVar(
    "moldy_delegated_child", default=None
)
_CHECK_INTERVAL = 1.0


@dataclass(slots=True, eq=False)
class ChildTaskLifecycle:
    """A linked DB actor, frozen at graph construction; never built from model arguments."""

    context: OrgContext
    conversation_id: UUID
    child_agent_id: UUID
    role: str
    session_scope: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None

    def session(self) -> AbstractAsyncContextManager[AsyncSession]:
        return self.session_scope() if self.session_scope else tenant_session(self.context)

    def middleware(self) -> ChildInstructionMiddleware:
        return ChildInstructionMiddleware(self)

    async def check(self, task: _Invocation) -> None:
        async with self.session() as db:
            await store.check_child_task(
                db, context=self.context, child_id=task.child_id, run_id=task.run_id
            )

    async def _finish(self, task: _Invocation, status: str, result: str | None = None) -> None:
        async with self.session() as db:
            await store.set_child_task_status(
                db,
                context=self.context,
                child_id=task.child_id,
                run_id=task.run_id,
                status=status,
                result=result,
            )
            await db.commit()

    async def reconcile(self, task: _Invocation, config: RunnableConfig | None = None) -> None:
        proof = await saved_child_step(
            self.context,
            self.conversation_id,
            task.run_id,
            task.tool_call_id,
            task.namespace,
            child_id=task.child_id,
            messages_by_id=task.sent,
            config=config,
        )
        if proof is None:
            return
        checkpoint, ids = proof
        async with self.session() as db:
            if ids:
                await store.confirm_child_instructions(
                    db,
                    context=self.context,
                    child_id=task.child_id,
                    run_id=task.run_id,
                    ids=ids,
                    checkpoint_id=checkpoint,
                )
            else:
                await store.record_child_checkpoint(
                    db,
                    context=self.context,
                    child_id=task.child_id,
                    run_id=task.run_id,
                    checkpoint_id=checkpoint,
                )
            await db.commit()
        for instruction_id in ids:
            task.sent.pop(instruction_id, None)

    async def wrap_dispatch(self, request: ToolCallRequest, handler: ToolHandler) -> ToolResult:
        runtime = request.runtime
        if runtime is None:
            raise ValueError("Missing child dispatch runtime")
        call_id = runtime.tool_call_id
        cfg = runtime.config.get("configurable") or {}
        namespace = cfg.get("checkpoint_ns")
        if (
            not call_id
            or call_id != request.tool_call.get("id")
            or str(cfg.get("thread_id")) != str(self.conversation_id)
            or str(cfg.get("moldy_tenant_id")) != str(self.context.tenant_id)
            or not isinstance(namespace, str)
            or not namespace
        ):
            raise ValueError("Unbound child invocation")
        run_id = UUID(str(cfg.get("moldy_run_id")))
        args = request.tool_call.get("args")
        description = args.get("description") if isinstance(args, Mapping) else None
        brief = (
            redact_protocol_data("messages", description) if isinstance(description, str) else None
        )
        try:
            async with self.session() as db:
                child_id = await store.begin_child_task(
                    db,
                    context=self.context,
                    conversation_id=self.conversation_id,
                    run_id=run_id,
                    tool_call_id=call_id,
                    child_agent_id=self.child_agent_id,
                    role=self.role,
                    checkpoint_ns=namespace,
                    brief=brief,
                    allow_create=settings.child_tasks_enabled,
                )
                await db.commit()
            if child_id is None:
                # An untracked call admitted under the legacy policy can retain its original
                # compiler and guards, including when another child resumed after opt-out.
                legacy = _invocation.set(self)
                try:
                    return await handler(request)
                finally:
                    _invocation.reset(legacy)
            task = _Invocation(self, child_id, run_id, call_id, namespace)
            token = _invocation.set(task)
            try:
                result = await self._watch_child(task, request, handler)
                await self.reconcile(task, runtime.config)
                summary = _tool_message(result, call_id)
                await self._finish(
                    task,
                    "failed" if summary and summary.status == "error" else "completed",
                    redact_protocol_data("messages", summary.text) if summary else None,
                )
                return result
            except GraphInterrupt:
                await self._finish(task, "awaiting_approval")
                raise
            except asyncio.CancelledError:
                # If the parent was already fenced, keep cancellation propagating.
                with suppress(ChildTaskStopped):
                    await self._finish(task, "cancelled")
                raise
            except ChildTaskStopped:
                raise
            except Exception:
                await self._finish(task, "failed")
                raise
            finally:
                _invocation.reset(token)
        except ChildTaskStopped:
            # One unavailable target must not cancel an unrelated sibling tool call.
            return ToolMessage(
                content="The delegated task stopped.",
                tool_call_id=call_id,
                name="task",
                status="error",
            )

    async def _watch_child(
        self, task: _Invocation, request: ToolCallRequest, handler: ToolHandler
    ) -> ToolResult:
        """Bound the delay for a cancellation on another worker, including inside a slow tool."""

        async def watch() -> None:
            while True:
                await asyncio.sleep(_CHECK_INTERVAL)
                try:
                    async with self.session() as db:
                        cancelled = await store.child_cancel_requested(
                            db, context=self.context, child_id=task.child_id, run_id=task.run_id
                        )
                except (AppError, DispatchDeniedError, SQLAlchemyError, TimeoutError):
                    # The call is already admitted. Fence new calls at their own boundary.
                    continue
                if cancelled:
                    raise ChildTaskStopped("cancelled")

        runner = asyncio.ensure_future(handler(request))
        monitor = asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait((runner, monitor), return_when=asyncio.FIRST_COMPLETED)
            if monitor in done:
                await monitor
            return await runner
        finally:
            for future in (runner, monitor):
                if not future.done():
                    future.cancel()
            await asyncio.gather(runner, monitor, return_exceptions=True)


def _tool_message(result: ToolResult, call_id: str) -> ToolMessage | None:
    if isinstance(result, ToolMessage):
        return result if result.tool_call_id == call_id else None
    if not isinstance(result.update, Mapping):
        return None
    return next(
        (
            m
            for m in reversed(result.update.get("messages", []))
            if isinstance(m, ToolMessage) and m.tool_call_id == call_id
        ),
        None,
    )


class ChildInstructionState(AgentState[Any]):
    moldy_child_model_receipts: NotRequired[Annotated[dict[str, Any], PrivateStateAttr]]


class ChildInstructionMiddleware(AgentMiddleware[Any, Any, Any]):
    """Place accepted input in the child's own checkpoint after outstanding tools settle."""

    state_schema = ChildInstructionState

    def __init__(self, lifecycle: ChildTaskLifecycle) -> None:
        self.lifecycle = lifecycle

    def _task(self) -> _Invocation | None:
        task = _invocation.get()
        if task is self.lifecycle:
            return None
        if not isinstance(task, _Invocation) or task.lifecycle is not self.lifecycle:
            raise ValueError("Missing authorized child invocation")
        return task

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        task = self._task()
        if task is None:
            return None
        await self.lifecycle.check(task)
        # The model must see all tool results before a new user message.
        outstanding: set[str] = set()
        for item in state["messages"]:
            if isinstance(item, AIMessage):
                outstanding.update(
                    call_id for call in item.tool_calls if (call_id := call.get("id"))
                )
            if isinstance(item, ToolMessage):
                outstanding.discard(item.tool_call_id)
        if outstanding:
            return None
        async with self.lifecycle.session() as db:
            instructions = await store.next_child_instructions(
                db, context=self.lifecycle.context, child_id=task.child_id, run_id=task.run_id
            )
            await db.commit()
        present = {item.id for item in state["messages"]}
        messages = []
        for instruction_id, text, freshly_claimed in instructions:
            message_id = child_instruction_message_id(instruction_id)
            # A prior worker may have delivered and compacted this instruction.
            # Without checkpoint proof it stays uncertain and is never inserted again.
            if freshly_claimed and message_id not in present:
                messages.append(
                    HumanMessage(
                        content=text, id=message_id, response_metadata={STEER_INPUT_MARKER: True}
                    )
                )
            task.sent[instruction_id] = text
        return {"messages": messages} if messages else None

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse | ExtendedModelResponse:
        task = self._task()
        if task is None:
            return await handler(request)
        await self.lifecycle.check(task)
        if not task.sent:
            return await handler(request)
        expected = {
            child_instruction_message_id(key): (str(key), text_digest(text))
            for key, text in task.sent.items()
        }
        response, matched = await observe_message_delivery(request, handler, expected)
        if not matched:
            return response
        proof = {
            "child": str(task.child_id),
            "run": str(task.run_id),
            "tool": task.tool_call_id,
            "outputs": {message_id: sorted(ids) for message_id, ids in matched.items()},
        }
        return ExtendedModelResponse(
            model_response=response, command=Command(update={CHILD_RECEIPTS_CHANNEL: proof})
        )

    async def aafter_model(self, state: AgentState, runtime: Runtime) -> None:
        task = self._task()
        if task is None:
            return
        await self.lifecycle.check(task)
        await self.lifecycle.reconcile(task)

    async def awrap_tool_call(self, request: ToolCallRequest, handler: ToolHandler) -> ToolResult:
        if (task := self._task()) is not None:
            await self.lifecycle.check(task)
        return await handler(request)


class DelegatedTaskDispatch(AgentMiddleware[Any, Any, Any]):
    """Track only task calls targeting a graph assembled from a verified child binding."""

    # Stock SubAgentMiddleware derives its private keys from the parent's schemas.
    # Exclude the child-only proof on both child input and return.
    state_schema = ChildInstructionState

    def __init__(self, lifecycles: Mapping[str, ChildTaskLifecycle]) -> None:
        self.lifecycles = dict(lifecycles)

    async def awrap_tool_call(self, request: ToolCallRequest, handler: ToolHandler) -> ToolResult:
        if request.tool_call.get("name") == "task":
            args = request.tool_call.get("args")
            role = args.get("subagent_type") if isinstance(args, Mapping) else None
            if isinstance(role, str) and (target := self.lifecycles.get(role)) is not None:
                return await target.wrap_dispatch(request, handler)
        return await handler(request)


def task_lifecycles(specs: Sequence[Mapping[str, Any]] | None) -> dict[str, ChildTaskLifecycle]:
    return {
        guard.lifecycle.role: guard.lifecycle
        for spec in specs or ()
        for guard in spec.get("middleware") or ()
        if isinstance(guard, ChildInstructionMiddleware)
        and guard.lifecycle.role == spec.get("name")
    }
