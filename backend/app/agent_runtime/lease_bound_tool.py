"""Generic lease leaf guard for tool dispatch (parent Task 10, internal I12).

``LeaseBoundToolMiddleware`` is an app-owned ``AgentMiddleware`` that exposes
zero tools and only implements ``awrap_tool_call``. For every tool call it:

1. maps the tool name to the participant's frozen census descriptor and
   denies unknown names, known never-dispatchable tools (``execute``,
   ``compact_conversation``, ``execute_in_skill`` — each with its own code) and
   un-intercepted ``task`` calls without SQL and without entering the handler;
2. opens a fresh ``open_dispatch_permit(kind="tool", ...)`` that admits exactly
   one ``handler(request)`` await; the permit's authority transaction is
   already released when the handler runs, so the tool call holds no lock,
   session or connection (a revocation committed meanwhile denies the next
   tool/provider/child boundary, not this in-flight call);
3. for ``live_application_resource`` tools, requires every addressed resource
   ID to be in the preloaded snapshot set; otherwise it denies before the
   handler with ``organization_archived`` (archive observed by the permit) or
   ``live_resource_not_prepared``.

The guard is bound to the lease: the census descriptor IDs must be a subset of
``lease.descriptor_ids`` (the only set permits are checked against), and when
the lease carries ``tool_access`` the census classes must be a subset of it.

Denials (including a ``DispatchDeniedError`` raised by the handler) are
returned as an error ``ToolMessage`` carrying the original ``tool_call_id`` so
the graph reaches a bounded terminal state instead of raising through the
ToolNode. The sync path is intentionally not implemented
(the installed base raises), so a lease graph cannot bypass the guard by
running synchronously.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.agent_runtime.lease_dispatch_permit import (
    DESCRIPTOR_NOT_PREPARED,
    DispatchDeniedError,
    SessionFactory,
    open_dispatch_permit,
)
from app.agent_runtime.runtime_config import PreparedRunLease
from app.agent_runtime.runtime_effective_capabilities import (
    CapabilityDescriptor,
    EffectiveCapabilityCensus,
)
from app.agent_runtime.tool_access_policy import (
    ORCHESTRATION_ADMISSION,
    denied_tool_code,
    requested_resource_ids,
)

LIVE_RESOURCE_NOT_PREPARED = "live_resource_not_prepared"
ORGANIZATION_ARCHIVED = "organization_archived"
ToolHandler = Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]]


@dataclass(frozen=True, slots=True)
class ToolDenial:
    tool_name: str
    tool_call_id: str
    code: str


class LeaseBoundToolMiddleware(AgentMiddleware):
    """Zero-tool leaf guard binding every tool dispatch to one prepared lease."""

    tools = ()  # the guard never contributes a tool schema

    def __init__(
        self,
        lease: PreparedRunLease,
        census: EffectiveCapabilityCensus,
        *,
        session_factory: SessionFactory,
    ) -> None:
        super().__init__()
        if not census.descriptor_ids <= lease.descriptor_ids:
            raise ValueError("census descriptors are not covered by the lease descriptor set")
        if lease.tool_access and not census.tool_access().items() <= lease.tool_access.items():
            raise ValueError("census tool access classes are not covered by the lease")
        self._lease = lease
        self._census = census
        self._allowed = lease.descriptor_ids
        self._session_factory = session_factory
        self.denials: list[ToolDenial] = []

    @property
    def name(self) -> str:
        return f"LeaseBoundToolMiddleware[{self._census.participant}]"

    def _deny(self, request: ToolCallRequest, code: str) -> ToolMessage:
        call = request.tool_call
        name = str(call.get("name") or "")
        call_id = str(call.get("id") or "")
        self.denials.append(ToolDenial(name, call_id, code))
        return ToolMessage(
            content=f"Tool call denied: {code}",
            name=name,
            tool_call_id=call_id,
            status="error",
        )

    def _resources_prepared(self, descriptor: CapabilityDescriptor, args: object) -> bool:
        if descriptor.resource_arg is None:
            return False
        requested = requested_resource_ids(args, descriptor.resource_arg)
        if requested is None:
            return False
        prepared = self._census.preloaded_resources.get(descriptor.name, frozenset())
        return all(resource_id in prepared for resource_id in requested)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: ToolHandler,
    ) -> ToolMessage | Command[Any]:
        call = request.tool_call
        name = str(call.get("name") or "")
        if name in self._census.denied_tool_names:
            return self._deny(request, denied_tool_code(name) or DESCRIPTOR_NOT_PREPARED)
        descriptor = self._census.tool_descriptors.get(name)
        if descriptor is None:
            return self._deny(request, DESCRIPTOR_NOT_PREPARED)
        if descriptor.operation_kind == ORCHESTRATION_ADMISSION:
            # ``task`` must be short-circuited by the prepared task wrapper first.
            return self._deny(request, LIVE_RESOURCE_NOT_PREPARED)
        try:
            async with open_dispatch_permit(
                self._lease,
                kind="tool",
                descriptor_id=descriptor.descriptor_id,
                allowed_descriptors=self._allowed,
                session_factory=self._session_factory,
            ) as permit:
                live = descriptor.access_class == "live_application_resource"
                if live and not self._resources_prepared(descriptor, call.get("args")):
                    code = (
                        ORGANIZATION_ARCHIVED
                        if permit.archived_bypass
                        else LIVE_RESOURCE_NOT_PREPARED
                    )
                    return self._deny(request, code)
                return await handler(request)
        except DispatchDeniedError as error:
            return self._deny(request, error.code)
