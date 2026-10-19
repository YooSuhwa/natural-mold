"""Effective capability census for prepared leases (parent Task 10, internal I12).

The census is built before the marker from the *effective* dispatch surface of
one participant (root or child): every tool name actually present in the
compiled ToolNode (selected registry/builtin/MCP/skill/app tools plus the
installed-generated filesystem/todo/task tools), every middleware instance,
every backend and every model provider. Middleware must be in the closed lease
allowlist (``lease_middleware_policy``) and every backend must be exactly a
``LeaseSnapshotBackend``. Each item receives one stable
descriptor ID. The resulting frozen ID set is what ``open_dispatch_permit``
checks and what the lease digest covers; any runtime name or ID outside it is
denied.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from app.agent_runtime.lease_middleware_policy import class_qualname, unsupported_middleware
from app.agent_runtime.lease_snapshot_backend import LeaseSnapshotBackend
from app.agent_runtime.runtime_config import ToolAccessClass
from app.agent_runtime.tool_access_policy import (
    DENIED_TOOLS,
    ToolAccessPolicyError,
    ToolOrigin,
    classify_tool,
    generated_tool_access,
)

DescriptorKind = Literal["tool", "middleware", "backend", "provider"]
SUPPORTED_PROVIDERS: frozenset[str] = frozenset(
    ("openai", "anthropic", "google", "custom", "openrouter", "openai_compatible")
)


class CapabilityCensusError(ValueError):
    """The effective surface is not closed; preparation stops before the marker."""

    def __init__(self, code: str, subject: str) -> None:
        super().__init__(f"{code}:{subject}")
        self.code = code
        self.subject = subject


@dataclass(frozen=True, slots=True)
class ProviderOrigin:
    """One model send closure: ``role`` is e.g. ``primary``, ``fallback:0``, ``summary``."""

    role: str
    provider: str
    model_name: str


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    descriptor_id: str
    kind: DescriptorKind
    name: str
    source: str
    access_class: ToolAccessClass | None = None
    operation_kind: str | None = None
    resource_arg: str | None = None


@dataclass(frozen=True, slots=True)
class EffectiveCapabilityCensus:
    participant: str
    descriptors: tuple[CapabilityDescriptor, ...]
    denied_tool_names: frozenset[str]
    preloaded_resources: Mapping[str, frozenset[str]] = field(repr=False)
    descriptor_ids: frozenset[str] = field(init=False)
    tool_descriptors: Mapping[str, CapabilityDescriptor] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        ids = frozenset(item.descriptor_id for item in self.descriptors)
        tools = {item.name: item for item in self.descriptors if item.kind == "tool"}
        object.__setattr__(self, "descriptor_ids", ids)
        object.__setattr__(self, "tool_descriptors", MappingProxyType(tools))

    def tool_access(self) -> dict[str, str]:
        """Participant-qualified ``tool_access`` material for the lease digest."""

        return {
            f"{self.participant}/{item.name}": str(item.access_class)
            for item in self.descriptors
            if item.kind == "tool" and item.access_class is not None
        }

    def digest(self) -> str:
        payload = {
            "participant": self.participant,
            "descriptors": sorted(
                [
                    item.descriptor_id,
                    item.access_class or "",
                    item.operation_kind or "",
                    item.resource_arg or "",
                ]
                for item in self.descriptors
            ),
            "denied": sorted(self.denied_tool_names),
            "resources": {key: sorted(value) for key, value in self.preloaded_resources.items()},
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def middleware_tool_names(middleware: Sequence[Any]) -> tuple[str, ...]:
    """Names of every tool the given middleware instances generate."""

    return tuple(str(getattr(tool, "name", "")) for m in middleware for tool in m.tools)


def tool_node_names(compiled_graph: Any) -> tuple[str, ...]:
    """Dispatchable tool names of an installed compiled agent graph."""

    node = compiled_graph.nodes.get("tools")
    if node is None:
        return ()
    return tuple(sorted(node.bound.tools_by_name))


def _tool_descriptors(
    participant: str,
    effective_tool_names: Iterable[str],
    selected: Mapping[str, ToolOrigin],
) -> tuple[list[CapabilityDescriptor], set[str]]:
    names = list(effective_tool_names)
    if len(names) != len(set(names)):
        raise CapabilityCensusError("capability_tool_duplicate", participant)
    descriptors: list[CapabilityDescriptor] = []
    denied: set[str] = set()
    for name in sorted(names):
        origin = selected.get(name)
        denial = DENIED_TOOLS.get(name)
        if denial is not None:
            if origin is not None and origin.source != denial[0]:
                raise CapabilityCensusError("capability_tool_shadowed", name)
            denied.add(name)
            continue
        if origin is None:
            access = generated_tool_access(name)
            if access is None:
                raise CapabilityCensusError("capability_tool_unknown", name)
            source = "generated"
        else:
            if generated_tool_access(name) is not None and origin.source != "generated":
                raise CapabilityCensusError("capability_tool_shadowed", name)
            try:
                access = classify_tool(origin)
            except ToolAccessPolicyError as error:
                raise CapabilityCensusError(error.code, name) from None
            source = origin.source
        descriptors.append(
            CapabilityDescriptor(
                descriptor_id=f"tool:{participant}:{source}:{name}",
                kind="tool",
                name=name,
                source=source,
                access_class=access.access_class,
                operation_kind=access.operation_kind,
                resource_arg=access.resource_arg,
            )
        )
    missing = set(selected) - set(names)
    if missing:
        raise CapabilityCensusError("capability_tool_not_effective", ",".join(sorted(missing)))
    return descriptors, denied


def build_effective_census(
    participant: str,
    *,
    effective_tool_names: Iterable[str],
    selected_tools: Sequence[ToolOrigin],
    middleware: Sequence[Any],
    backends: Mapping[str, Any],
    providers: Sequence[ProviderOrigin],
    preloaded_resources: Mapping[str, Iterable[str]] | None = None,
) -> EffectiveCapabilityCensus:
    """Close one participant's effective surface or raise ``CapabilityCensusError``."""

    if not participant or ":" in participant:
        raise CapabilityCensusError("capability_participant_invalid", participant)
    selected = {origin.name: origin for origin in selected_tools}
    if len(selected) != len(selected_tools):
        raise CapabilityCensusError("capability_tool_duplicate", participant)
    descriptors, denied = _tool_descriptors(participant, effective_tool_names, selected)
    live_tools = {
        item.name
        for item in descriptors
        if item.access_class == "live_application_resource" and item.resource_arg
    }
    resources = {key: frozenset(value) for key, value in (preloaded_resources or {}).items()}
    stray = set(resources) - live_tools
    if stray:
        raise CapabilityCensusError("capability_resource_tool_unknown", ",".join(sorted(stray)))
    unsupported = unsupported_middleware(middleware)
    if unsupported is not None:
        raise CapabilityCensusError("capability_middleware_unsupported", unsupported)
    for index, item in enumerate(middleware):
        qualname = class_qualname(item)
        descriptors.append(
            CapabilityDescriptor(
                f"middleware:{participant}:{index}:{qualname}", "middleware", qualname, "installed"
            )
        )
    for role, backend in sorted(backends.items()):
        if type(backend) is not LeaseSnapshotBackend:
            raise CapabilityCensusError("capability_backend_unsupported", role)
        qualname = class_qualname(backend)
        descriptors.append(
            CapabilityDescriptor(
                f"backend:{participant}:{role}:{qualname}", "backend", role, qualname
            )
        )
    for origin in providers:
        if origin.provider not in SUPPORTED_PROVIDERS:
            raise CapabilityCensusError("capability_provider_unsupported", origin.provider)
        descriptors.append(
            CapabilityDescriptor(
                f"provider:{participant}:{origin.role}:{origin.provider}:{origin.model_name}",
                "provider",
                origin.role,
                origin.provider,
            )
        )
    ids = [item.descriptor_id for item in descriptors]
    if len(ids) != len(set(ids)):
        raise CapabilityCensusError("capability_descriptor_duplicate", participant)
    return EffectiveCapabilityCensus(
        participant=participant,
        descriptors=tuple(descriptors),
        denied_tool_names=frozenset(denied),
        preloaded_resources=MappingProxyType(resources),
    )


def lease_descriptor_ids(censuses: Iterable[EffectiveCapabilityCensus]) -> frozenset[str]:
    """Union of every participant's frozen descriptor IDs for ``PreparedRunLease``."""

    return frozenset(item for census in censuses for item in census.descriptor_ids)
