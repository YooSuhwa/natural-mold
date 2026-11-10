"""Whether a tool call asks a person first (approval-modes-plan 3.1-3.2): one decision function.

Layers, top first; the first that decides wins, and nothing below the company layers can
lift what they set:

1. company ``block``, or a tool the company switched off → the tool is hidden (and an
   emergency stop refuses the run earlier);
2. the company's always-ask tools, and a level the company forces (``approval``) → ask;
   a tool the company fixed to one mode (tool-governance-plan 3.4) → that mode;
3. the agent's per-tool setting: ``always`` → ask; ``when`` → ask while the rule over the
   call's args holds, else run; ``never`` → run;
4. allowed for this conversation (chat only) → run;
5. the company's default for the tool;
6. the tool's own default (its row's, else its MCP server's; tool-governance-plan 3.3):
   ``always`` → ask; ``when`` → ask while the rule holds; ``never`` → run;
7. the agent's mode for the run path: ``auto`` → run; ``ask_all`` → the risk default.

Where the company restricted a tool's modes, layers 3-7 only count while their mode is one
the company allows; a result outside them asks. A company policy for one tool wins over its
level's action, in both directions.

"Run" is only reachable where the company left the level ``allow`` or ``approval_relaxable``,
because layer 2 decided everything else. Runs nobody watches turn an ask into a wait or a
BLOCKED answer afterwards (``app/approvals/unattended.py``); guardrail approval rules (③) add
their own conditional asks afterwards. The run assembly (``apply``) and the effective-settings
API (``effective``) call ``decide`` — never two copies of these rules.

The agent's layers (3, 4, 7) apply to the root agent; a declarative child keeps the company
layers (also where they relax) and default asks, and of a tool's own default only what
tightens (``root=False``): below the company, approvals only get stricter there (plan 3.1
"하위 에이전트").

Settings name a tool by its resource key (``app.tools.keys``, tool-governance-plan 3.2). A
setting saved under a tool name (before keys) is still read: one that tightens applies to
every tool with that name, one that relaxes only when a single tool carries the name.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool

from app.agent_runtime.runtime_policy import ToolApprovalPolicyV1, ToolApprovalToolV1
from app.authz.audit_actions import APPROVAL_AUTO_ALLOWED
from app.conditions import Condition, holds
from app.guardrails.tool_risk import (
    DEFAULT_ACTIONS,
    MODES,
    ToolPolicy,
    ToolRiskPolicy,
    approval_entry,
    level_of,
)
from app.tools.keys import ToolRef, ambiguous_names, ref_of
from app.tools.risk import ToolRiskLevel

Kind = Literal["ask", "auto", "when", "hidden"]
Path = Literal["chat", "trigger", "workflow", "api"]
PATHS: Final[tuple[Path, ...]] = ("chat", "trigger", "workflow", "api")
BUILTIN_TOOLS: Final = ("write_file", "edit_file", "execute")
# Levels a conversation grant may lift without the company opening them first (plan Q5).
GRANTABLE_BY_DEFAULT: Final = frozenset({ToolRiskLevel.WRITE_INTERNAL})

AUTO_ALLOWED: Final = APPROVAL_AUTO_ALLOWED
REASONS: Final[dict[str, str]] = {
    "company_block": "회사 정책: 차단",
    "company_always": "회사 정책: 항상 사람이 승인",
    "company_forced": "회사 정책: 이 위험 등급은 승인 필요",
    "company_tool_disabled": "회사 정책: 이 도구는 사용 금지",
    "company_tool_locked": "회사 정책: 이 도구의 승인 방식 고정",
    "company_tool_default": "회사 정책: 이 도구의 기본값",
    "tool_always": "도구 설정: 항상 묻기",
    "tool_when": "도구 설정: 조건이 맞으면 묻기",
    "tool_never": "도구 설정: 묻지 않기",
    "conversation_grant": "이 대화 동안 허용",
    "tool_default": "도구 기본값",
    "server_default": "MCP 서버 기본값",
    "agent_mode": "에이전트 모드: 자동",
    "risk_default": "위험 등급 기본값",
}


@dataclass(frozen=True, slots=True)
class Decision:
    kind: Kind
    layer: str
    rule: Condition | None = None
    # A second rule that also asks when it holds (the company's, beside the agent's).
    also: Condition | None = None

    @property
    def reason(self) -> str:
        return REASONS[self.layer]


_ALL_MODES: Final = frozenset(MODES)
# Layers no agent setting, conversation grant or tool default can change.
COMPANY_FIXED: Final = frozenset(
    {
        "company_block",
        "company_always",
        "company_forced",
        "company_tool_disabled",
        "company_tool_locked",
    }
)


def _action(company: ToolRiskPolicy | None, level: ToolRiskLevel) -> str:
    return company.action(level) if company is not None else DEFAULT_ACTIONS[level.value]


def _company_rule(found: ToolPolicy | None) -> Condition | None:
    """The rule of the company's ``when``; ``None`` when there is none or it cannot be read."""
    if found is None or not found.rule:
        return None
    try:
        return ToolApprovalToolV1.model_validate({"mode": "when", "rule": dict(found.rule)}).rule
    except ValueError:
        return None


def _by_mode(mode: str, layer: str, rule: Condition | None) -> Decision:
    if mode == "never":
        return Decision("auto", layer)
    if mode == "when" and rule is not None:
        return Decision("when", layer, rule)
    return Decision("ask", layer)  # ``always``, or a ``when`` whose rule cannot be read


def _ref(tool: ToolRef | str) -> ToolRef:
    return tool if isinstance(tool, ToolRef) else ref_of(tool)


def _setting(
    settings: ToolApprovalPolicyV1 | None, ref: ToolRef, ambiguous: bool
) -> ToolApprovalToolV1 | None:
    if settings is None:
        return None
    found = settings.tools.get(ref.key)
    if found is not None:
        return found
    named = settings.tools.get(ref.name)  # saved by name, before keys
    if named is not None and ambiguous and named.mode == "never":
        return None  # which tool it meant is unknown: the ask stays
    return named


def _default(ref: ToolRef) -> tuple[ToolApprovalToolV1, str] | None:
    """The tool's own default and the layer it came from; one that cannot be read asks."""
    raw = ref.default
    if not raw:
        return None
    layer = "server_default" if raw.get("layer") == "server" else "tool_default"
    try:
        return ToolApprovalToolV1.model_validate(
            {"mode": raw.get("mode"), "rule": raw.get("rule")}
        ), layer
    except ValueError:
        return ToolApprovalToolV1(mode="always"), layer


def decide(
    tool: ToolRef | str,
    level: ToolRiskLevel,
    path: Path,
    *,
    settings: ToolApprovalPolicyV1 | None,
    company: ToolRiskPolicy | None,
    grants: frozenset[str] = frozenset(),
    legacy_always: frozenset[str] = frozenset(),
    ambiguous: bool = False,
    root: bool = True,
) -> Decision:
    """The decision for one tool on one path (pure; same inputs, same answer).

    ``tool`` is the tool's ref (a bare string is a built-in by that name); ``ambiguous``
    says another tool of the same agent carries the same name. ``root=False`` decides a
    declarative child's tool: the agent's own layers do not reach it, and the tool's default
    cannot lift an ask there.
    """
    ref = _ref(tool)
    name = ref.name
    found = company.tool_policy(ref) if company is not None else None
    if company is not None and company.blocks(ref, level):
        switched_off = found is not None and found.enabled is False
        return Decision("hidden", "company_tool_disabled" if switched_off else "company_block")
    action = company.tool_action(ref, level) if company is not None else _action(None, level)
    if company is not None and company.always_asks(ref):
        return Decision("ask", "company_always")
    company_rule = _company_rule(found)
    if found is not None and found.allowed is not None:
        allowed = found.allowed  # the tool's own modes win over its level's action
        if len(allowed) == 1:
            return _by_mode(next(iter(allowed)), "company_tool_locked", company_rule)
    elif action == "approval":
        return Decision("ask", "company_forced")
    else:
        allowed = _ALL_MODES
    if not root:
        settings, grants, legacy_always = None, frozenset(), frozenset()
    setting = _setting(settings, ref, ambiguous)
    if setting is not None and setting.mode in allowed:
        if setting.mode == "always":
            return Decision("ask", "tool_always")
        if setting.mode == "when":
            # Where the company forbids "never", its own rule keeps asking beside the agent's.
            also = company_rule if "never" not in allowed else None
            return Decision("when", "tool_when", setting.rule, also)
        return Decision("auto", "tool_never")
    if (name in legacy_always or ref.key in legacy_always) and "always" in allowed:
        # An ask the agent set through the human_in_the_loop middleware (before this feature).
        return Decision("ask", "tool_always")
    lifts = "never" in allowed
    if lifts and path == "chat" and (ref.key in grants or (name in grants and not ambiguous)):
        return Decision("auto", "conversation_grant")
    if found is not None and found.default_mode is not None:
        return _by_mode(found.default_mode, "company_tool_default", company_rule)
    default = _default(ref)
    if default is not None and default[0].mode in allowed:
        own, layer = default
        if own.mode == "always":
            return Decision("ask", layer)
        if own.mode == "when":
            return Decision("when", layer, own.rule)
        if root:
            return Decision("auto", layer)
    mode = getattr(settings.paths, path) if settings is not None else "ask_all"
    if mode == "auto" and lifts:
        return Decision("auto", "agent_mode")
    asks_by_default = action == "approval_relaxable" or (
        action != "allow" and level != ToolRiskLevel.READ_ONLY
    )
    if ("always" if asks_by_default else "never") not in allowed:
        return Decision("ask", "company_tool_default")  # the usual answer is not allowed
    return Decision("ask" if asks_by_default else "auto", "risk_default")


def grantable(
    tool: ToolRef | str,
    level: ToolRiskLevel,
    *,
    settings: ToolApprovalPolicyV1 | None,
    company: ToolRiskPolicy | None,
    ambiguous: bool = False,
) -> bool:
    """Whether "allow for this conversation" may lift this tool (plan Q5)."""
    ref = _ref(tool)
    decision = decide(ref, level, "chat", settings=settings, company=company, ambiguous=ambiguous)
    if decision.layer in COMPANY_FIXED or decision.layer == "tool_always":
        return False
    if decision.kind == "auto":
        return False  # nothing to lift
    found = company.tool_policy(ref) if company is not None else None
    if found is not None and found.allowed is not None:
        return "never" in found.allowed  # the company said which modes this tool may take
    action = _action(company, level)
    return level in GRANTABLE_BY_DEFAULT or action in ("allow", "approval_relaxable")


@dataclass(frozen=True, slots=True)
class ToolApprovalPlan:
    """What a run needs to decide its tools (pinned with the run's config)."""

    path: Path
    settings: ToolApprovalPolicyV1 | None = None
    company: ToolRiskPolicy | None = None
    grants: frozenset[str] = frozenset()
    legacy_always: frozenset[str] = frozenset()
    # Who and where, for the audit of a risky call that ran without asking (plan Q9).
    audit: AuditTarget | None = None

    # Some tool of the run (the root's or a child's) carries its own approval default.
    defaults: bool = False

    @property
    def relaxes(self) -> bool:
        """``False`` when the agent's settings can only keep today's asks."""
        has_settings = self.settings is not None and not self.settings.is_default
        return has_settings or (self.path == "chat" and bool(self.grants))

    @property
    def acts(self) -> bool:
        """``False`` when applying the plan would change nothing (the run skips it)."""
        return self.relaxes or self.defaults or bool(self.company and self.company.tools)

    def decide(
        self, tool: BaseTool | str, *, ambiguous: bool = False, root: bool = True
    ) -> Decision:
        return decide(
            ref_of(tool),
            level_of(tool),
            self.path,
            settings=self.settings,
            company=self.company,
            grants=self.grants,
            legacy_always=self.legacy_always,
            ambiguous=ambiguous,
            root=root,
        )


@dataclass(frozen=True, slots=True)
class AuditTarget:
    tenant_id: Any
    org_id: Any
    user_id: Any
    agent_id: str | None
    thread_id: str | None


def legacy_always_from(middleware_configs: Iterable[Mapping[str, Any]] | None) -> frozenset[str]:
    """Tools an agent set to ask through the ``human_in_the_loop`` middleware (plan 3.3)."""
    for config in middleware_configs or []:
        if config.get("type") != "human_in_the_loop":
            continue
        raw = (config.get("params") or {}).get("interrupt_on")
        if isinstance(raw, Mapping):
            return frozenset(str(name) for name, value in raw.items() if value)
    return frozenset()


def has_default(tools_config: Iterable[Mapping[str, Any]] | None) -> bool:
    return any(config.get("approval_default") for config in tools_config or [])


def _has_defaults(cfg: Any) -> bool:
    if has_default(cfg.tools_config):
        return True
    for spec in getattr(cfg, "subagents_config", None) or []:
        if any(ref_of(tool).default for tool in spec.get("tools") or []):
            return True
    return False


def plan_for(cfg: Any) -> ToolApprovalPlan | None:
    """The run's plan from its pinned config; ``None`` on a path with no settings (subagent)."""
    path = cfg.run_path if cfg.run_path in PATHS else None
    if path is None:
        return None
    context = getattr(cfg, "org_context", None)
    return ToolApprovalPlan(
        path=path,
        settings=cfg.runtime_policy.effective.tool_approval,
        company=cfg.tool_risk,
        grants=frozenset(cfg.tool_grants),
        legacy_always=legacy_always_from(cfg.middleware_configs),
        defaults=_has_defaults(cfg),
        audit=AuditTarget(
            getattr(context, "tenant_id", None),
            getattr(context, "org_id", None),
            getattr(context, "user_id", None),
            cfg.agent_id,
            cfg.thread_id,
        )
        if context is not None
        else None,
    )


def _args_text(args: Any) -> str:
    try:
        return json.dumps(args, ensure_ascii=False, indent=2, sort_keys=True)[:2000]
    except (TypeError, ValueError):
        return str(args)[:2000]


def _described(entry: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """An ask that says why (the approval card and the inbox show the description)."""

    def description(tool_call: Any, _state: Any, _runtime: Any) -> str:
        return (
            f"승인이 필요합니다 ({reason}).\n\n도구: {tool_call.get('name')}\n"
            f"인자: {_args_text(tool_call.get('args'))}"
        )

    return {**dict(entry), "description": description}


def _when(
    entry: Mapping[str, Any], rule: Condition, reason: str, also: Condition | None = None
) -> dict[str, Any]:
    rules = [rule, also] if also is not None else [rule]

    def when(request: Any) -> bool:
        try:
            values = {"args": request.tool_call.get("args") or {}}
            return any(holds(one, values) for one in rules)
        except Exception:  # noqa: BLE001 - a rule that cannot be read asks (plan 3.4)
            return True

    return {**_described(entry, reason), "when": when}


@dataclass(slots=True)
class Applied:
    interrupt_on: dict[str, Any] | None
    # Tools at write level or above that will run without asking (audited when called).
    auto_allowed: dict[str, str] = field(default_factory=dict)


def apply(
    interrupt_on: dict[str, Any] | bool | None,
    tools: Iterable[BaseTool],
    plan: ToolApprovalPlan,
    *,
    root: bool = True,
) -> Applied:
    """An agent's ``interrupt_on`` with the plan's decisions (after the company step).

    For a declarative child (``root=False``) an entry is only ever added: an ask the child
    already has stays as it is.
    """
    if not isinstance(interrupt_on, dict):
        return Applied(interrupt_on if isinstance(interrupt_on, dict) else None)
    if not root:
        return Applied(_tightened(interrupt_on, list(tools), plan))
    result = dict(interrupt_on)
    auto: dict[str, str] = {}
    targets: list[BaseTool | str] = [*tools, *BUILTIN_TOOLS]
    twice = ambiguous_names(ref_of(tool) for tool in targets)
    for tool in targets:
        # ``interrupt_on`` is keyed by the name the model calls (a run may have renamed it).
        name = tool if isinstance(tool, str) else tool.name
        decision = plan.decide(tool, ambiguous=ref_of(tool).name in twice)
        if decision.kind == "hidden":
            result.pop(name, None)
            continue
        base = result.get(name) or approval_entry(tool)
        if decision.kind == "ask":
            if decision.layer != "risk_default":  # the default ask keeps its own wording
                result[name] = _described(base, decision.reason)
        elif decision.kind == "when" and decision.rule is not None:
            result[name] = _when(base, decision.rule, decision.reason, decision.also)
        else:
            result.pop(name, None)
            if level_of(tool) != ToolRiskLevel.READ_ONLY:
                auto[name] = decision.layer
    return Applied(result, auto)


def _tightened(
    interrupt_on: dict[str, Any], tools: list[BaseTool], plan: ToolApprovalPlan
) -> dict[str, Any]:
    result = dict(interrupt_on)
    for tool in tools:
        decision = plan.decide(tool, root=False)
        company = decision.layer in ("company_tool_locked", "company_tool_default")
        if not company and decision.layer not in ("tool_default", "server_default"):
            continue  # the company step already set the rest
        if decision.kind == "ask":
            result[tool.name] = _described(
                result.get(tool.name) or approval_entry(tool), decision.reason
            )
        elif decision.kind == "when" and decision.rule is not None:
            if company or not result.get(tool.name):
                result[tool.name] = _when(approval_entry(tool), decision.rule, decision.reason)
        elif company and decision.kind == "auto":
            result.pop(tool.name, None)  # the company's own answer for this tool
    return result


# How an ask reaches a person on each path (plan Q4): chat shows the card; a workflow step
# waits in the inbox; schedules and API deployments wait or answer BLOCKED by their own setting.
DELIVERY: Final[dict[Path, str]] = {
    "chat": "card",
    "workflow": "wait",
    "trigger": "per_schedule",
    "api": "per_deployment",
}


@dataclass(frozen=True, slots=True)
class Effective:
    """One cell of the "실제 적용" table."""

    tool: str  # the resource key
    level: str
    path: Path
    kind: Kind
    layer: str
    reason: str
    delivery: str  # how an ask reaches a person on this path (``DELIVERY``)
    rule: Condition | None = None


def effective(
    tools: Iterable[tuple[ToolRef, ToolRiskLevel]],
    *,
    settings: ToolApprovalPolicyV1 | None,
    company: ToolRiskPolicy | None,
    legacy_always: frozenset[str] = frozenset(),
) -> list[Effective]:
    """Every tool × path, as a run decides it (the same ``decide``)."""
    cells: list[Effective] = []
    listed = list(tools)
    twice = ambiguous_names(ref for ref, _ in listed)
    for ref, level in listed:
        for path in PATHS:
            decision = decide(
                ref,
                level,
                path,
                settings=settings,
                company=company,
                legacy_always=legacy_always,
                ambiguous=ref.name in twice,
            )
            cells.append(
                Effective(
                    ref.key,
                    level.value,
                    path,
                    decision.kind,
                    decision.layer,
                    decision.reason,
                    DELIVERY[path],
                    decision.rule,
                )
            )
    return cells


__all__ = [
    "BUILTIN_TOOLS",
    "COMPANY_FIXED",
    "DELIVERY",
    "PATHS",
    "REASONS",
    "AUTO_ALLOWED",
    "Applied",
    "AuditTarget",
    "AutoAllowedAuditMiddleware",
    "Decision",
    "Effective",
    "ToolApprovalPlan",
    "apply",
    "decide",
    "effective",
    "grantable",
    "has_default",
    "legacy_always_from",
    "plan_for",
]


class AutoAllowedAuditMiddleware(AgentMiddleware):
    """Audits each call of a write-level-or-above tool that ran without asking (plan Q9).

    Best effort: the call runs whether the audit row can be written or not.
    """

    def __init__(self, layers: Mapping[str, str], target: AuditTarget) -> None:
        super().__init__()
        self.layers = dict(layers)
        self.target = target

    @property
    def name(self) -> str:  # type: ignore[override]
        return "MoldyToolApprovalAuditMiddleware"

    async def _record(self, call: Mapping[str, Any]) -> None:
        from app.authz.org_context import OrgContext
        from app.database import async_session, tenant_session
        from app.services import audit_service

        tool = str(call.get("name"))
        target = self.target
        try:
            context = OrgContext(
                org_id=target.org_id, tenant_id=target.tenant_id, user_id=target.user_id
            )
            async with tenant_session(context, maker=async_session) as db:
                await audit_service.record_event(
                    db,
                    actor_type="agent",
                    actor_user_id=target.user_id,
                    owner_user_id=target.user_id,
                    action=AUTO_ALLOWED,
                    target_type="agent",
                    target_id=target.agent_id,
                    outcome="success",
                    reason_code=self.layers[tool],
                    metadata={
                        "tool": tool,
                        "layer": self.layers[tool],
                        "thread_id": target.thread_id,
                    },
                )
                await db.commit()
        except Exception:  # noqa: BLE001 - never fail the call over its audit row
            import logging

            logging.getLogger(__name__).warning("auto-allowed audit failed tool=%s", tool)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        call = request.tool_call
        if call.get("name") in self.layers:
            await self._record(call)
        return await handler(request)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        return handler(request)  # async runs only; sync calls are not audited
