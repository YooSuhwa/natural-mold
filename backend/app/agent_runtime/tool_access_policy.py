"""Prepared-run tool access classification (parent Task 10, internal I12).

Every effective tool of a prepared lease is assigned exactly one of the four
``ToolAccessClass`` values before the marker:

* ``snapshot_safe`` — reads only frozen snapshot bytes or pure computation;
* ``resolved_external`` — calls an external API with credentials resolved at
  preparation (registry, MCP, web search);
* ``run_output`` — writes only the exact run scratch/offload/output roots or
  run-local graph state;
* ``live_application_resource`` — touches live application rows and may only
  address resource IDs preloaded into the lease.

Classification is closed: an unknown source, an unknown builtin key, an
installed-generated tool that is not listed here, or an app tool without an
explicit declaration is rejected. Nothing defaults to ``snapshot_safe``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from app.agent_runtime.runtime_config import TOOL_ACCESS_CLASSES, ToolAccessClass

ToolSource = Literal["builtin", "registry", "mcp", "skill", "app", "generated"]
TOOL_SOURCES: frozenset[str] = frozenset(
    ("builtin", "registry", "mcp", "skill", "app", "generated")
)
ORCHESTRATION_ADMISSION = "orchestration_admission"


class ToolAccessPolicyError(ValueError):
    """A tool could not be classified; preparation must stop before the marker."""

    def __init__(self, code: str, tool_name: str) -> None:
        super().__init__(f"{code}:{tool_name}")
        self.code = code
        self.tool_name = tool_name


@dataclass(frozen=True, slots=True)
class ToolOrigin:
    """Trusted preparation-time declaration of where a selected tool came from.

    ``key`` is the registry/builtin ``definition_key``, the MCP tool identity,
    or the generated tool name. ``access_class``/``resource_arg`` are honored
    only for ``source="app"`` tools, which must declare them explicitly.
    """

    name: str
    source: ToolSource
    key: str
    access_class: ToolAccessClass | None = None
    resource_arg: str | None = None


@dataclass(frozen=True, slots=True)
class ToolAccess:
    access_class: ToolAccessClass
    operation_kind: str | None = None
    resource_arg: str | None = None


_GENERATED: Mapping[str, ToolAccess] = MappingProxyType(
    {
        # deepagents FilesystemMiddleware reads go to the snapshot backend.
        "ls": ToolAccess("snapshot_safe"),
        "read_file": ToolAccess("snapshot_safe"),
        "glob": ToolAccess("snapshot_safe"),
        "grep": ToolAccess("snapshot_safe"),
        # Mutations are confined to the exact run roots by the snapshot backend.
        "write_file": ToolAccess("run_output"),
        "edit_file": ToolAccess("run_output"),
        "delete": ToolAccess("run_output"),
        # Run-local graph state.
        "write_todos": ToolAccess("run_output"),
        # Child launch is a typed live operation owned by the prepared task wrapper.
        "task": ToolAccess("live_application_resource", ORCHESTRATION_ADMISSION),
    }
)
# Known tools that are never dispatchable under a lease: name -> (only legitimate
# source, denial code). The census records them as denied (no descriptor) and the
# leaf guard returns the code without opening a permit or entering the handler.
# * ``execute``: shell execution has no snapshot/run-root confinement.
# * ``compact_conversation``: performs a provider send inside a tool handler that
#   is not routed through a lease-owned model, so no provider permit would admit it.
# * ``execute_in_skill``: ``skill_executor`` runs a subprocess in the *live* skill
#   directory with injected credentials and network. Remapping it onto
#   snapshot-materialised skill bytes is future work; until then it is denied.
DENIED_TOOLS: Mapping[str, tuple[ToolSource, str]] = MappingProxyType(
    {
        "execute": ("generated", "descriptor_not_prepared"),
        "compact_conversation": ("generated", "compaction_not_leased"),
        "execute_in_skill": ("skill", "skill_execution_not_snapshotted"),
    }
)
GENERATED_TOOL_NAMES: frozenset[str] = frozenset(_GENERATED) | frozenset(
    name for name, (source, _code) in DENIED_TOOLS.items() if source == "generated"
)

_BUILTIN: Mapping[str, ToolAccessClass] = MappingProxyType(
    {
        "builtin:current_datetime": "snapshot_safe",
        "builtin:resolve_relative_date": "snapshot_safe",
        "builtin:e2e_ui_data_demo": "snapshot_safe",
        "builtin:web_search": "resolved_external",
        "builtin:web_scraper": "resolved_external",
        "builtin:e2e_scripted_search": "resolved_external",
    }
)
_SKILL: Mapping[str, ToolAccessClass] = MappingProxyType({})


def generated_tool_access(name: str) -> ToolAccess | None:
    """Return the fixed class of an installed-generated tool, ``None`` if denied/unknown."""

    return _GENERATED.get(name)


def denied_tool_code(name: str) -> str | None:
    """Denial code of a known never-dispatchable tool, ``None`` if not denied."""

    denial = DENIED_TOOLS.get(name)
    return None if denial is None else denial[1]


def classify_tool(origin: ToolOrigin) -> ToolAccess:
    """Classify one selected tool or raise ``ToolAccessPolicyError``."""

    name = origin.name
    if not name or origin.source not in TOOL_SOURCES:
        raise ToolAccessPolicyError("tool_source_unknown", name)
    if origin.source != "app" and (origin.access_class or origin.resource_arg):
        raise ToolAccessPolicyError("tool_access_override_forbidden", name)
    denied = denied_tool_code(name)
    if denied is not None:
        raise ToolAccessPolicyError(denied, name)
    if origin.source == "generated":
        access = _GENERATED.get(origin.key)
        if access is None or origin.key != name:
            raise ToolAccessPolicyError("tool_generated_unknown", name)
        return access
    if origin.source == "builtin":
        builtin = _BUILTIN.get(origin.key)
        if builtin is None:
            raise ToolAccessPolicyError("tool_builtin_unknown", name)
        return ToolAccess(builtin)
    if origin.source == "skill":
        skill = _SKILL.get(origin.key)
        if skill is None:
            raise ToolAccessPolicyError("tool_skill_unknown", name)
        return ToolAccess(skill)
    if origin.source in ("registry", "mcp"):
        if not origin.key:
            raise ToolAccessPolicyError("tool_external_key_missing", name)
        return ToolAccess("resolved_external")
    return _classify_app_tool(origin)


def _classify_app_tool(origin: ToolOrigin) -> ToolAccess:
    declared = origin.access_class
    if declared is None or declared not in TOOL_ACCESS_CLASSES:
        raise ToolAccessPolicyError("tool_access_undeclared", origin.name)
    if declared == "live_application_resource":
        if not origin.resource_arg:
            raise ToolAccessPolicyError("tool_resource_arg_missing", origin.name)
        return ToolAccess(declared, resource_arg=origin.resource_arg)
    if origin.resource_arg:
        raise ToolAccessPolicyError("tool_resource_arg_unexpected", origin.name)
    return ToolAccess(declared)


def requested_resource_ids(args: object, resource_arg: str) -> tuple[str, ...] | None:
    """Return the resource IDs a live tool call addresses, ``None`` if malformed.

    A missing argument, a non-string/non-list value, or an empty list is
    malformed: a live tool must name exactly which preloaded rows it touches.
    """

    if not isinstance(args, Mapping) or resource_arg not in args:
        return None
    value = args[resource_arg]
    if isinstance(value, str):
        return (value,) if value else None
    if isinstance(value, list | tuple) and value and all(isinstance(i, str) and i for i in value):
        return tuple(value)
    return None
