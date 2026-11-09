"""Company default actions per tool risk level (guardrail-minimum-plan.md C5, step 5).

``tenants.settings["guardrails"]["tool_risk"]`` maps each risk level (``app.tools.risk``)
to ``allow`` (no approval), ``approval`` (the approval card; an agent cannot switch it
off) or ``block`` (the model never gets the tool and a call is refused). Without a saved
choice a level keeps today's behaviour: read-only tools run, every other level needs
approval. Runs nobody watches (schedules, Agent API) apply the same approvals unattended:
the call waits for the approval inbox or answers BLOCKED (``app/approvals/unattended.py``);
a company-blocked tool is not run at all.

The policy applies to the root agent and every declarative child (``apply_to_agent``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, cast, get_args

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from app.tools.risk import (
    ToolRiskLevel,
    default_deepagents_interrupt_policy,
    get_tool_risk,
    interrupt_policy_for_tool,
    risk_from_tool_config,
)

# ``approval_relaxable``: asked by default, but an agent's tool approval settings may run it
# without asking (approval-modes-plan Q2); ``approval`` stays forced.
RiskAction = Literal["allow", "approval", "approval_relaxable", "block"]
MAX_ALWAYS_ASK: Final = 200
ACTIONS: Final = frozenset(get_args(RiskAction))
LEVELS: Final = tuple(level.value for level in ToolRiskLevel)
SETTINGS_KEY: Final = "tool_risk"
DEFAULT_ACTIONS: Final[Mapping[str, RiskAction]] = {
    level: ("allow" if level == ToolRiskLevel.READ_ONLY.value else "approval") for level in LEVELS
}
# Deep Agents' own tools, which carry no Moldy risk metadata.
_BUILTIN_LEVELS: Final[Mapping[str, ToolRiskLevel]] = {
    "write_file": ToolRiskLevel.WRITE_INTERNAL,
    "edit_file": ToolRiskLevel.WRITE_INTERNAL,
    "execute": ToolRiskLevel.CODE_EXECUTION,
}
BLOCKED_MESSAGE: Final = "This tool is blocked by the company policy; it was not run."


class InvalidToolRiskError(ValueError):
    """A level or action outside the known names."""


@dataclass(frozen=True, slots=True)
class ToolRiskPolicy:
    actions: Mapping[str, RiskAction]
    # Tools a person always approves, whatever any agent setting says (plan Q2).
    always_ask: frozenset[str] = frozenset()

    def action(self, level: ToolRiskLevel | str) -> RiskAction:
        key = level.value if isinstance(level, ToolRiskLevel) else level
        return self.actions.get(key, DEFAULT_ACTIONS.get(key, "approval"))

    @property
    def is_default(self) -> bool:
        return not self.always_ask and all(
            self.action(level) == DEFAULT_ACTIONS[level] for level in LEVELS
        )


def actions_from_json(raw: Any) -> dict[str, RiskAction]:
    """The full level → action map, defaults filled in; raises on unknown names."""
    result: dict[str, RiskAction] = dict(DEFAULT_ACTIONS)
    if raw is None:
        return result
    if not isinstance(raw, Mapping):
        raise InvalidToolRiskError("tool_risk must be an object")
    for level, action in raw.items():
        if level not in DEFAULT_ACTIONS:
            raise InvalidToolRiskError(f"unknown risk level {level!r}")
        if action not in ACTIONS:
            raise InvalidToolRiskError(
                f"risk level {level}: action must be one of {sorted(ACTIONS)}"
            )
        result[level] = cast(RiskAction, action)
    return result


def always_ask_from_json(raw: Any) -> frozenset[str]:
    """The company's always-ask tool names; raises on a malformed list."""
    if raw is None:
        return frozenset()
    if not isinstance(raw, list) or len(raw) > MAX_ALWAYS_ASK:
        raise InvalidToolRiskError(f"always_ask_tools must be a list of at most {MAX_ALWAYS_ASK}")
    if not all(isinstance(name, str) and 0 < len(name) <= 128 for name in raw):
        raise InvalidToolRiskError("always_ask_tools must name tools")
    return frozenset(raw)


def policy_from_actions(
    actions: Mapping[str, str] | None, always_ask: frozenset[str] = frozenset()
) -> ToolRiskPolicy | None:
    """``None`` when every level keeps its default and no tool is always asked."""
    if not actions and not always_ask:
        return None
    policy = ToolRiskPolicy(actions_from_json(actions), frozenset(always_ask))
    return None if policy.is_default else policy


def level_of(tool: BaseTool | str) -> ToolRiskLevel:
    """A tool's risk level (Deep Agents' own tools by name)."""
    return _level(tool)


def approval_entry(tool: BaseTool | str) -> dict[str, Any]:
    """The interrupt entry that asks before this tool runs."""
    return _approval_entry(tool)


def _level(tool: BaseTool | str) -> ToolRiskLevel:
    name = tool if isinstance(tool, str) else tool.name
    if name in _BUILTIN_LEVELS:
        return _BUILTIN_LEVELS[name]
    if isinstance(tool, str):
        return ToolRiskLevel.READ_ONLY
    return get_tool_risk(tool).risk_level


def blocked_names(policy: ToolRiskPolicy, tools: Iterable[BaseTool]) -> frozenset[str]:
    names = {tool.name for tool in tools if policy.action(_level(tool)) == "block"}
    names |= {name for name in _BUILTIN_LEVELS if policy.action(_BUILTIN_LEVELS[name]) == "block"}
    return frozenset(names)


def config_blocked(policy: ToolRiskPolicy | None, config: Mapping[str, Any]) -> bool:
    """A stored tool config (agent tool row) the company blocks."""
    if policy is None:
        return False
    return policy.action(risk_from_tool_config(dict(config)).risk_level) == "block"


def _default_entry(tool: BaseTool | str) -> Any:
    if isinstance(tool, str):
        return default_deepagents_interrupt_policy().get(tool)
    return interrupt_policy_for_tool(tool).get(tool.name)


def _approval_entry(tool: BaseTool | str) -> dict[str, Any]:
    entry = _default_entry(tool)
    if isinstance(entry, dict):
        return entry
    return {"allowed_decisions": ["approve", "reject"]}


def interrupts_for(
    interrupt_on: dict[str, Any] | bool | None,
    tools: Iterable[BaseTool],
    policy: ToolRiskPolicy,
) -> dict[str, Any] | bool | None:
    """``interrupt_on`` with the company actions applied to these tools.

    ``None``/``False`` (scheduled runs) stays as is: nobody can approve there. ``allow``
    removes only the default approval, so an approval the agent asked for stays.
    """
    if not isinstance(interrupt_on, dict):
        return interrupt_on
    result = dict(interrupt_on)
    targets: list[BaseTool | str] = [*tools, *_BUILTIN_LEVELS]
    for tool in targets:
        name = tool if isinstance(tool, str) else tool.name
        action = policy.action(_level(tool))
        if action == "block":
            result.pop(name, None)
        elif action in ("approval", "approval_relaxable") or name in policy.always_ask:
            # Relaxable asks too; only the root agent's own settings may lift it afterwards
            # (``app.approvals.tool_modes``), never a child's or the default.
            if not result.get(name):
                result[name] = _approval_entry(tool)
        elif name in result and result[name] == _default_entry(tool):
            del result[name]
    return result


class ToolRiskMiddleware(AgentMiddleware):
    """Hides blocked tools from the model and refuses a call that names one anyway."""

    def __init__(self, blocked: frozenset[str]) -> None:
        super().__init__()
        self.blocked = blocked

    @property
    def name(self) -> str:  # type: ignore[override]
        return "MoldyToolRiskMiddleware"

    def _visible(self, request: ModelRequest) -> ModelRequest:
        tools = [tool for tool in request.tools if _tool_name(tool) not in self.blocked]
        return request.override(tools=tools) if len(tools) != len(request.tools) else request

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Any]) -> Any:
        return handler(self._visible(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[Any]]
    ) -> Any:
        return await handler(self._visible(request))

    def _refusal(self, request: Any) -> ToolMessage | None:
        call = request.tool_call
        if call.get("name") not in self.blocked:
            return None
        return ToolMessage(
            content=BLOCKED_MESSAGE, tool_call_id=call.get("id") or "", status="error"
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._refusal(request) or handler(request)

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        refused = self._refusal(request)
        return refused if refused is not None else await handler(request)


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, BaseTool):
        return tool.name
    if isinstance(tool, Mapping):
        function = tool.get("function")
        if isinstance(function, Mapping):
            return cast(str | None, function.get("name"))
        return cast(str | None, tool.get("name"))
    return None


@dataclass(frozen=True, slots=True)
class AppliedToolRisk:
    tools: list[BaseTool]
    interrupt_on: dict[str, Any] | bool | None
    middleware: list[Any]
    subagents: list[dict[str, Any]]


def apply_to_agent(
    policy: ToolRiskPolicy,
    *,
    tools: list[BaseTool],
    interrupt_on: dict[str, Any] | bool | None,
    middleware: list[Any],
    subagents: list[dict[str, Any]],
) -> AppliedToolRisk:
    """The root and each declarative child with blocked tools removed and approvals set."""
    blocked = blocked_names(policy, tools)
    children: list[dict[str, Any]] = []
    for spec in subagents:
        if "runnable" in spec or "graph_id" in spec:
            children.append(spec)
            continue
        child_tools = list(spec.get("tools") or [])
        child_blocked = blocked_names(policy, child_tools)
        child = {
            **spec,
            "tools": [tool for tool in child_tools if tool.name not in child_blocked],
            "middleware": [*(spec.get("middleware") or []), ToolRiskMiddleware(child_blocked)],
        }
        if "interrupt_on" in spec:
            child["interrupt_on"] = interrupts_for(spec["interrupt_on"], child_tools, policy)
        children.append(child)
    return AppliedToolRisk(
        tools=[tool for tool in tools if tool.name not in blocked],
        interrupt_on=interrupts_for(interrupt_on, tools, policy),
        middleware=[*middleware, ToolRiskMiddleware(blocked)],
        subagents=children,
    )
