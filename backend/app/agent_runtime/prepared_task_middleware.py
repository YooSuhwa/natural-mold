"""Zero-tool ``task`` interceptor for prepared leases (parent Task 10, I12).

``PreparedTaskDispatchMiddleware`` sits first among the app-owned tool-call
wrappers. Non-``task`` calls delegate exactly once to ``handler``. A ``task``
call never reaches the handler (so neither the generic leaf guard nor the
installed stock task closure runs): the stock arguments are validated, the
runtime tool-call ID must equal the outer call ID, the response-format token
maps to an exact frozen variant, a one-shot launch is reserved under that
invocation ID, and the selected precompiled runnable is awaited once after the
admission permit has closed. The installed ``SubAgentMiddleware`` remains the
only ``task`` schema provider; this middleware exposes no tools.

The ledger ``parent_task_id`` is the invoking task path: ``root`` at the root
and, below it, the path the parent handler stamped into the child config, so
two invocations of one intermediate child never share nested winners. Nested
children are compiled with ``[interceptor, *spec.middleware, SubAgentMiddleware]``
spliced into the installed-parity child stack (``prepared_child_stack``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Any, Protocol

from deepagents.middleware.subagents import (
    SUBAGENT_RESPONSE_FORMAT_CONFIG_KEY,
    CompiledSubAgent,
    SubAgentMiddleware,
    TaskToolSchema,
    create_sub_agent,
)
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from pydantic import ValidationError

from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_child_launch import ChildLaunchReservation
from app.agent_runtime.prepared_child_stack import prepared_child_spec
from app.agent_runtime.prepared_subagent_catalog import (
    ROOT_PATH,
    PreparedChildCatalog,
    PreparedChildCatalogError,
    response_variant_id,
)
from app.agent_runtime.prepared_task_tool_handler import (
    TASK_PATH_KEY,
    PreparedStateProjector,
    run_prepared_child,
)

TASK_TOOL_NAME = "task"
type RunnableKey = tuple[str, str]
type ToolResult = ToolMessage | Command[Any]


class ChildLaunchAdmission(Protocol):
    async def reserve_child_launch(
        self,
        *,
        parent_task_id: str,
        parent_path: str,
        child_id: str,
        child_token: str,
        invocation_id: str,
    ) -> ChildLaunchReservation: ...


def _configurable(config: Any) -> Mapping[str, Any]:
    configurable = config.get("configurable") if isinstance(config, Mapping) else None
    return configurable if isinstance(configurable, Mapping) else {}


def invoking_task_id(config: Any, parent_path: str) -> str:
    """Return the invoking task path; fail closed when it does not fit the level."""

    path = _configurable(config).get(TASK_PATH_KEY)
    if parent_path == ROOT_PATH and path is None:
        return ROOT_PATH
    if parent_path == ROOT_PATH or not isinstance(path, str) or not path.startswith("root/"):
        raise DispatchDeniedError("task_chain_invalid")
    return path


def requested_variant(config: Any) -> str:
    """Map the configured response-format token to a variant ID without compiling."""

    value = _configurable(config).get(SUBAGENT_RESPONSE_FORMAT_CONFIG_KEY)
    if isinstance(value, str):
        return value
    try:
        return response_variant_id(value)
    except PreparedChildCatalogError as error:
        raise DispatchDeniedError(error.code) from None


class PreparedTaskDispatchMiddleware(AgentMiddleware[Any, Any, Any]):
    def __init__(
        self,
        *,
        catalog: PreparedChildCatalog,
        parent_path: str,
        runnables: Mapping[RunnableKey, Any],
        projector: PreparedStateProjector,
        admission_factory: Callable[[], ChildLaunchAdmission],
    ) -> None:
        super().__init__()
        self.tools = []
        self._level = catalog.level(parent_path)
        self._parent_path = parent_path
        ids = {child.child_id for child in self._level.values()}
        self._runnables = MappingProxyType({k: v for k, v in runnables.items() if k[0] in ids})
        self._projector = projector
        self._admission_factory = admission_factory

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolResult],
    ) -> ToolResult:
        if request.tool_call.get("name") != TASK_TOOL_NAME:
            return handler(request)
        raise DispatchDeniedError("prepared_task_requires_async")

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolResult]],
    ) -> ToolResult:
        if request.tool_call.get("name") != TASK_TOOL_NAME:
            return await handler(request)
        return await self._dispatch(request)

    async def _dispatch(self, request: ToolCallRequest) -> ToolResult:
        invocation_id = request.tool_call.get("id")
        runtime = request.runtime
        if not invocation_id or runtime is None or runtime.tool_call_id != invocation_id:
            raise DispatchDeniedError("task_invocation_id_mismatch")
        try:
            args = TaskToolSchema.model_validate(request.tool_call.get("args") or {})
        except ValidationError:
            return ToolMessage(
                "Invalid task arguments",
                name=TASK_TOOL_NAME,
                tool_call_id=invocation_id,
                status="error",
            )
        child = self._level.get(args.subagent_type)
        if child is None:
            allowed = ", ".join(f"`{name}`" for name in self._level)
            return ToolMessage(
                f"We cannot invoke subagent {args.subagent_type} because it does not exist, "
                f"the only allowed types are {allowed}",
                name=TASK_TOOL_NAME,
                tool_call_id=invocation_id,
            )
        parent_task_id = invoking_task_id(runtime.config, self._parent_path)
        variant = requested_variant(runtime.config)
        runnable = self._runnables.get((child.child_id, variant))
        if variant not in child.variants or runnable is None:
            raise DispatchDeniedError("response_variant_not_prepared")
        child_input = self._projector.child_input(request.state, args.description)
        reservation = await self._admission_factory().reserve_child_launch(
            parent_task_id=parent_task_id,
            parent_path=self._parent_path,
            child_id=child.child_id,
            child_token=child.token,
            invocation_id=invocation_id,
        )
        return await run_prepared_child(
            reservation,
            runnable,
            child_input,
            invocation_id=invocation_id,
            projector=self._projector,
        )


def compiled_subagent_entries(
    catalog: PreparedChildCatalog,
    runnables: Mapping[RunnableKey, Any],
    parent_path: str = ROOT_PATH,
) -> list[CompiledSubAgent]:
    """Return the compiled base children handed to installed Deep Agents."""

    entries: list[CompiledSubAgent] = []
    for child in catalog.level(parent_path).values():
        runnable = runnables[(child.child_id, "default")]
        entries.append(
            CompiledSubAgent(name=child.name, description=child.description, runnable=runnable)
        )
    return entries


def precompile_prepared_children(
    catalog: PreparedChildCatalog,
    *,
    backend: Any,
    projector: PreparedStateProjector,
    admission_factory: Callable[[], ChildLaunchAdmission],
    compile_child: Callable[..., Any] = create_sub_agent,
    interrupt_on: dict[str, Any] | None = None,
    skills: list[str] | None = None,
    parent_middleware: Sequence[AgentMiddleware[Any, Any, Any]] = (),
) -> Mapping[RunnableKey, Any]:
    """Compile every ``(child_id, variant_id)`` bottom-up before the marker.

    ``interrupt_on``, ``skills`` and ``parent_middleware`` are the root's, as
    passed to the installed ``create_deep_agent``; nested levels inherit their
    parent child's raw ``interrupt_on``.
    """

    by_depth = sorted(catalog.children.values(), key=lambda c: c.child_id.count("/"))
    inherited: dict[str, Any] = {ROOT_PATH: interrupt_on}
    for child in by_depth:
        inherited[child.child_id] = child.spec.get("interrupt_on", inherited[child.parent_path])
    runnables: dict[RunnableKey, Any] = {}
    for child in reversed(by_depth):
        if child.origin == "trusted_compiled":
            runnables[(child.child_id, "default")] = child.spec["runnable"]
            continue
        head: list[AgentMiddleware[Any, Any, Any]] = []
        tail: list[AgentMiddleware[Any, Any, Any]] = []
        if child.children:
            head.append(
                PreparedTaskDispatchMiddleware(
                    catalog=catalog,
                    parent_path=child.child_id,
                    runnables=runnables,
                    projector=projector,
                    admission_factory=admission_factory,
                )
            )
            entries = compiled_subagent_entries(catalog, runnables, child.child_id)
            tail.append(SubAgentMiddleware(backend=backend, subagents=entries))
        spec = prepared_child_spec(
            child,
            backend=backend,
            interrupt_on=inherited[child.parent_path],
            skills=skills,
            parent_middleware=parent_middleware,
            head=head,
            tail=tail,
        )
        for variant in child.variants:
            runnables[(child.child_id, variant)] = compile_child(
                spec, response_format=child.variant_values[variant]
            )
    return MappingProxyType(runnables)
