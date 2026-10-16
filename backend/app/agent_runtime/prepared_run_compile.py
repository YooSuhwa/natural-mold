"""Installed-compiler assembly for prepared leases (parent Task 10, internal I15).

Children are compiled bottom-up through the frozen I12 constructors: each
child's installed-parity stack (``prepared_child_spec``) receives the app head
``[PreparedTaskDispatchMiddleware?, LeaseBoundToolMiddleware?]`` so the prepared
interceptor is the first app tool-call wrapper and the generic leaf guard sits
immediately after it, ahead of the child's own custom middleware and the stock
``SubAgentMiddleware`` tail. Every response-format variant is compiled. The
root is compiled through ``build_agent_impl`` with only compiled base children
(including ``general-purpose``), so installed Deep Agents supplies exactly one
stock ``task`` schema. A *probe* pass (guards absent, placeholder child
runnables, admission refused) reports the effective tool surface the census
needs before the lease token can be bound; the final pass must reproduce it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Any

from deepagents import create_deep_agent
from deepagents.middleware.subagents import SubAgentMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableLambda

from app.agent_runtime.deep_agent_factory import DeepAgentFactoryBindings, build_agent_impl
from app.agent_runtime.lease_dispatch_permit import DispatchDeniedError
from app.agent_runtime.prepared_child_launch import ChildLaunchReservation
from app.agent_runtime.prepared_child_stack import prepared_child_spec
from app.agent_runtime.prepared_subagent_catalog import ROOT_PATH, PreparedChildCatalog
from app.agent_runtime.prepared_task_middleware import (
    ChildLaunchAdmission,
    PreparedTaskDispatchMiddleware,
    RunnableKey,
    compiled_subagent_entries,
)
from app.agent_runtime.prepared_task_tool_handler import PreparedStateProjector

type Middleware = AgentMiddleware[Any, Any, Any]
type AdmissionFactory = Callable[[], ChildLaunchAdmission]


class PreparedCompileError(RuntimeError):
    def __init__(self, code: str, subject: str = "") -> None:
        super().__init__(f"{code}:{subject}" if subject else code)
        self.code = code
        self.subject = subject


class _RefusedAdmission:
    async def reserve_child_launch(self, **_kwargs: str) -> ChildLaunchReservation:
        raise DispatchDeniedError("probe_graph_not_dispatchable")


def refused_admission() -> ChildLaunchAdmission:
    return _RefusedAdmission()


def _placeholder(_spec: dict[str, Any], *, response_format: Any = None) -> Any:
    async def run(_state: dict[str, Any]) -> dict[str, Any]:
        raise DispatchDeniedError("probe_graph_not_dispatchable")

    del response_format
    return RunnableLambda(run, name="prepared-probe-child")


def spec_tool_names(spec: Mapping[str, Any]) -> tuple[str, ...]:
    """Tool names the installed ``create_agent`` puts in this child's ToolNode."""

    names = [str(getattr(tool, "name", "")) for tool in spec.get("tools") or ()]
    for middleware in spec.get("middleware") or ():
        names.extend(str(getattr(tool, "name", "")) for tool in getattr(middleware, "tools", ()))
    return tuple(sorted(names))


def runnable_tool_names(runnable: Any) -> tuple[str, ...]:
    """Tool names of a compiled child graph's ToolNode; opaque runnables fail closed."""

    nodes = getattr(runnable, "nodes", None)
    if not isinstance(nodes, Mapping):
        raise PreparedCompileError("child_runnable_unverifiable")
    node = nodes.get("tools")
    return () if node is None else tuple(sorted(node.bound.tools_by_name))


def compile_children(
    catalog: PreparedChildCatalog,
    *,
    backend: Any,
    projector: PreparedStateProjector,
    admission_factory: AdmissionFactory,
    guards: Mapping[str, Middleware] | None,
    compile_child: Callable[..., Any] | None,
    interrupt_on: dict[str, Any] | None,
    skills: list[str] | None,
    parent_middleware: Sequence[Middleware],
) -> tuple[Mapping[RunnableKey, Any], Mapping[str, tuple[str, ...]]]:
    """Compile every ``(child_id, variant)`` bottom-up; ``None`` compiler means probe."""

    compiler = compile_child or _placeholder
    by_depth = sorted(catalog.children.values(), key=lambda c: c.child_id.count("/"))
    inherited: dict[str, Any] = {ROOT_PATH: interrupt_on}
    for child in by_depth:
        inherited[child.child_id] = child.spec.get("interrupt_on", inherited[child.parent_path])
    runnables: dict[RunnableKey, Any] = {}
    surfaces: dict[str, tuple[str, ...]] = {}
    for child in reversed(by_depth):
        head: list[Middleware] = []
        tail: list[Middleware] = []
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
        if guards is not None:
            head.append(guards[child.child_id])
        spec = prepared_child_spec(
            child,
            backend=backend,
            interrupt_on=inherited[child.parent_path],
            skills=skills,
            parent_middleware=parent_middleware,
            head=head,
            tail=tail,
        )
        surfaces[child.child_id] = spec_tool_names(spec)
        for variant in child.variants:
            runnables[(child.child_id, variant)] = compiler(
                spec, response_format=child.variant_values[variant]
            )
    return MappingProxyType(runnables), MappingProxyType(surfaces)


def _as_is(middleware: Sequence[Any] | None, **_kwargs: Any) -> list[Any]:
    return list(middleware or ())


def _never_normalize(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
    raise PreparedCompileError("stock_subagent_normalization_forbidden")


def compile_root(
    *,
    model: Any,
    tools: Sequence[Any],
    system_prompt: str,
    catalog: PreparedChildCatalog,
    runnables: Mapping[RunnableKey, Any],
    projector: PreparedStateProjector,
    admission_factory: AdmissionFactory,
    app_middleware: Sequence[Middleware],
    backend: Any,
    checkpointer: Any,
    interrupt_on: dict[str, Any] | None,
    skills: list[str] | None,
    memory: list[str] | None,
) -> Any:
    """Compile the root with ``[interceptor, *app_middleware]`` as the app slice."""

    interceptor = PreparedTaskDispatchMiddleware(
        catalog=catalog,
        parent_path=ROOT_PATH,
        runnables=runnables,
        projector=projector,
        admission_factory=admission_factory,
    )
    bindings = DeepAgentFactoryBindings(
        create_deep_agent=create_deep_agent,  # type: ignore[arg-type]
        with_compatibility=_as_is,
        normalize_subagents=_never_normalize,
    )
    return build_agent_impl(
        model,
        list(tools),
        system_prompt,
        middleware=list(app_middleware),
        interrupt_on=interrupt_on,
        checkpointer=checkpointer,
        backend=backend,
        skills=skills,
        memory=memory,
        subagents=compiled_subagent_entries(catalog, runnables),  # type: ignore[arg-type]
        bindings=bindings,
        prepared_task_middleware=interceptor,
    )
