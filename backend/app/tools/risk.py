"""Tool risk metadata used by HITL and scheduled trigger guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlparse

from langchain_core.tools import BaseTool

DecisionType = Literal["approve", "edit", "reject", "respond"]
RISK_METADATA_KEY = "moldy_risk"


class ToolRiskLevel(StrEnum):
    READ_ONLY = "read_only"
    WRITE_INTERNAL = "write_internal"
    EXTERNAL_MUTATION = "external_mutation"
    CODE_EXECUTION = "code_execution"
    UNKNOWN = "unknown"


_DEFAULT_APPROVAL_DECISIONS: dict[ToolRiskLevel, tuple[DecisionType, ...]] = {
    ToolRiskLevel.READ_ONLY: (),
    ToolRiskLevel.WRITE_INTERNAL: ("approve", "edit", "reject"),
    ToolRiskLevel.EXTERNAL_MUTATION: ("approve", "edit", "reject"),
    ToolRiskLevel.CODE_EXECUTION: ("approve", "reject"),
    ToolRiskLevel.UNKNOWN: ("approve", "reject"),
}
_TRIGGER_BLOCKED_LEVELS = {
    ToolRiskLevel.WRITE_INTERNAL,
    ToolRiskLevel.EXTERNAL_MUTATION,
    ToolRiskLevel.CODE_EXECUTION,
    ToolRiskLevel.UNKNOWN,
}
_TRUSTED_READ_ONLY_MCP_HOSTS = frozenset(
    {
        "hancom-gw-mcp.apps.orca.cloud.hancom.com",
    }
)
_TRUSTED_READ_ONLY_LOCAL_MCP_PORTS = frozenset({18001, 18002, 18003, 18004})
_READ_ONLY_MCP_NAME_PREFIXES = (
    "get_",
    "list_",
    "search_",
    "read_",
    "fetch_",
    "lookup_",
    "find_",
    "query_",
    "select_",
)
_READ_ONLY_MCP_DESCRIPTION_HINTS = (
    "조회",
    "목록",
    "검색",
    "상세",
    "현황",
    "read",
    "list",
    "search",
    "lookup",
    "fetch",
)


@dataclass(frozen=True)
class ToolRiskMetadata:
    risk_level: ToolRiskLevel = ToolRiskLevel.READ_ONLY
    requires_approval: bool = False
    allowed_decisions: tuple[DecisionType, ...] = ()
    trigger_safe: bool = True
    reason: str | None = None


@dataclass(frozen=True)
class TriggerBlockedTool:
    name: str
    risk_level: ToolRiskLevel
    reason: str


def risk_metadata_dict(
    risk_level: ToolRiskLevel | str = ToolRiskLevel.READ_ONLY,
    *,
    requires_approval: bool | None = None,
    allowed_decisions: tuple[DecisionType, ...] | list[DecisionType] | None = None,
    trigger_safe: bool | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Return a JSON-friendly risk metadata dictionary."""

    risk = _coerce_risk_level(risk_level)
    decisions = tuple(allowed_decisions or _DEFAULT_APPROVAL_DECISIONS[risk])
    if requires_approval is None:
        requires_approval = bool(decisions)
    if trigger_safe is None:
        trigger_safe = risk not in _TRIGGER_BLOCKED_LEVELS
    return {
        "risk_level": risk.value,
        "requires_approval": requires_approval,
        "allowed_decisions": list(decisions),
        "trigger_safe": trigger_safe,
        "reason": reason,
    }


def coerce_tool_risk(value: Any) -> ToolRiskMetadata:
    """Normalize arbitrary metadata into ``ToolRiskMetadata``."""

    if isinstance(value, ToolRiskMetadata):
        return value
    if not isinstance(value, dict):
        value = {}
    risk = _coerce_risk_level(value.get("risk_level", ToolRiskLevel.READ_ONLY))
    decisions_raw = value.get("allowed_decisions")
    decisions = tuple(decisions_raw or _DEFAULT_APPROVAL_DECISIONS[risk])
    requires_approval = value.get("requires_approval")
    if requires_approval is None:
        requires_approval = bool(decisions)
    trigger_safe = value.get("trigger_safe")
    if trigger_safe is None:
        trigger_safe = risk not in _TRIGGER_BLOCKED_LEVELS
    return ToolRiskMetadata(
        risk_level=risk,
        requires_approval=bool(requires_approval),
        allowed_decisions=decisions,
        trigger_safe=bool(trigger_safe),
        reason=value.get("reason"),
    )


def attach_tool_risk(tool: BaseTool, metadata: ToolRiskMetadata | dict[str, Any]) -> BaseTool:
    """Attach Moldy risk metadata to a LangChain tool."""

    risk = coerce_tool_risk(metadata)
    current = dict(getattr(tool, "metadata", None) or {})
    current[RISK_METADATA_KEY] = risk_metadata_dict(
        risk.risk_level,
        requires_approval=risk.requires_approval,
        allowed_decisions=list(risk.allowed_decisions),
        trigger_safe=risk.trigger_safe,
        reason=risk.reason,
    )
    try:
        tool.metadata = current
    except Exception:  # noqa: BLE001 - pydantic model fallback
        object.__setattr__(tool, "metadata", current)
    return tool


def get_tool_risk(tool: BaseTool) -> ToolRiskMetadata:
    return coerce_tool_risk((getattr(tool, "metadata", None) or {}).get(RISK_METADATA_KEY))


def risk_from_definition(definition: Any) -> ToolRiskMetadata:
    return coerce_tool_risk(
        risk_metadata_dict(
            getattr(definition, "risk_level", ToolRiskLevel.READ_ONLY),
            requires_approval=getattr(definition, "requires_approval", None),
            allowed_decisions=getattr(definition, "allowed_decisions", None),
            trigger_safe=getattr(definition, "trigger_safe", None),
            reason=getattr(definition, "risk_reason", None),
        )
    )


def builtin_tool_risk(definition_key: str) -> ToolRiskMetadata:
    if definition_key in {
        "builtin:web_search",
        "builtin:web_scraper",
        "builtin:current_datetime",
        "builtin:resolve_relative_date",
        # E2E-only deterministic scripted search (gated by
        # e2e_scripted_model_enabled). READ_ONLY like the real web_search so the
        # search-group fixture streams to completion without a HITL interrupt.
        "builtin:e2e_scripted_search",
        # E2E-only generative-UI demo tool (gated by e2e_scripted_model_enabled).
        # READ_ONLY so the demo fixture streams to completion without a HITL
        # interrupt (it only returns a JSON ui_data payload).
        "builtin:e2e_ui_data_demo",
    }:
        return coerce_tool_risk(risk_metadata_dict(ToolRiskLevel.READ_ONLY))
    return coerce_tool_risk(
        risk_metadata_dict(
            ToolRiskLevel.UNKNOWN,
            reason=f"unknown builtin tool '{definition_key}'",
        )
    )


def _mcp_metadata_read_only(metadata: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    return metadata.get("readOnlyHint") is True


def _is_trusted_read_only_mcp_url(raw_url: Any) -> bool:
    if not isinstance(raw_url, str) or not raw_url:
        return False
    parsed = urlparse(raw_url)
    host = parsed.hostname or ""
    if host in _TRUSTED_READ_ONLY_MCP_HOSTS:
        return True
    return host in {"localhost", "127.0.0.1", "::1"} and (
        parsed.port in _TRUSTED_READ_ONLY_LOCAL_MCP_PORTS
    )


def _trusted_mcp_config_url(config: dict[str, Any] | None) -> bool:
    if not config:
        return False
    return _is_trusted_read_only_mcp_url(config.get("mcp_server_url") or config.get("url"))


def _looks_like_read_only_mcp_tool(name: str, description: str | None) -> bool:
    lowered_name = name.lower()
    if lowered_name.startswith(_READ_ONLY_MCP_NAME_PREFIXES):
        return True
    lowered_description = (description or "").lower()
    return any(hint in lowered_description for hint in _READ_ONLY_MCP_DESCRIPTION_HINTS)


def _trusted_mcp_config_read_only(config: dict[str, Any] | None, fallback_name: str) -> bool:
    if not _trusted_mcp_config_url(config):
        return False
    assert config is not None
    name = str(config.get("mcp_tool_name") or config.get("name") or fallback_name)
    description = config.get("description")
    desc = description if isinstance(description, str) else None
    return _looks_like_read_only_mcp_tool(name, desc)


def mcp_tool_risk(
    name: str,
    *,
    metadata: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> ToolRiskMetadata:
    if _trusted_mcp_config_url(config) and (
        _mcp_metadata_read_only(metadata) or _trusted_mcp_config_read_only(config, name)
    ):
        return coerce_tool_risk(
            risk_metadata_dict(
                ToolRiskLevel.READ_ONLY,
                reason=f"MCP tool '{name}' is marked read-only",
            )
        )
    return coerce_tool_risk(
        risk_metadata_dict(
            ToolRiskLevel.EXTERNAL_MUTATION,
            allowed_decisions=("approve", "reject"),
            trigger_safe=False,
            reason=f"MCP tool '{name}' can perform remote side effects",
        )
    )


def execute_in_skill_risk() -> ToolRiskMetadata:
    return coerce_tool_risk(
        risk_metadata_dict(
            ToolRiskLevel.CODE_EXECUTION,
            allowed_decisions=("approve", "reject"),
            trigger_safe=False,
            reason="execute_in_skill runs subprocess code",
        )
    )


def default_deepagents_interrupt_policy() -> dict[str, Any]:
    return {
        "write_file": {"allowed_decisions": ["approve", "reject"]},
        "edit_file": {"allowed_decisions": ["approve", "edit", "reject"]},
        "execute": {"allowed_decisions": ["approve", "reject"]},
    }


def interrupt_policy_for_tool(tool: BaseTool) -> dict[str, Any]:
    risk = get_tool_risk(tool)
    if not risk.requires_approval:
        return {}
    return {tool.name: {"allowed_decisions": list(risk.allowed_decisions)}}


def merge_interrupt_policies(
    base: dict[str, Any], explicit: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge user policy without allowing it to disable required approvals."""

    merged = dict(base)
    for tool_name, config in (explicit or {}).items():
        if config is False and tool_name in merged:
            continue
        merged[tool_name] = config
    return merged


def trigger_blocked_tools(
    tool_configs: list[dict[str, Any]],
    *,
    has_agent_skills: bool,
) -> list[TriggerBlockedTool]:
    blocked: list[TriggerBlockedTool] = []
    for config in tool_configs:
        name = str(config.get("name") or config.get("definition_key") or "tool")
        risk = risk_from_tool_config(config)
        if risk.trigger_safe:
            continue
        blocked.append(
            TriggerBlockedTool(
                name=name,
                risk_level=risk.risk_level,
                reason=risk.reason or f"{risk.risk_level.value} tools are blocked in triggers",
            )
        )
    if has_agent_skills:
        risk = execute_in_skill_risk()
        blocked.append(
            TriggerBlockedTool(
                name="execute_in_skill",
                risk_level=risk.risk_level,
                reason=risk.reason or "skill execution is blocked in triggers",
            )
        )
    return blocked


def risk_from_tool_config(config: dict[str, Any]) -> ToolRiskMetadata:
    definition_key = str(config.get("definition_key") or "")
    if definition_key == "mcp":
        return mcp_tool_risk(
            str(config.get("name") or config.get("mcp_tool_name") or "mcp"),
            metadata=config.get("mcp_tool_metadata"),
            config=config,
        )
    if definition_key.startswith("builtin:"):
        return builtin_tool_risk(definition_key)

    from app.tools.registry import registry

    definition = registry.get(definition_key)
    if definition is None:
        return coerce_tool_risk(
            risk_metadata_dict(
                ToolRiskLevel.UNKNOWN,
                reason=f"unknown tool definition '{definition_key}'",
            )
        )
    return risk_from_definition(definition)


def format_trigger_block_reason(blocked: list[TriggerBlockedTool]) -> str:
    details = ", ".join(f"{item.name} ({item.risk_level.value}: {item.reason})" for item in blocked)
    return f"Blocked by tool risk policy: {details}"


def _coerce_risk_level(value: ToolRiskLevel | str) -> ToolRiskLevel:
    try:
        return value if isinstance(value, ToolRiskLevel) else ToolRiskLevel(str(value))
    except ValueError:
        return ToolRiskLevel.UNKNOWN
